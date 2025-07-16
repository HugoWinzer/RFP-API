import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from googleapiclient.discovery import build
from google.oauth2 import service_account
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import OpenAIEmbeddings
from openai import OpenAI
import gspread

app = Flask(__name__)
CORS(app)

def get_google_credentials(scopes):
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT")
    if raw_json is None:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT environment variable is not set")
    
    try:
        # First parse: unwrap string if needed
        parsed_string = json.loads(raw_json)
        info = json.loads(parsed_string) if isinstance(parsed_string, str) else parsed_string
        return service_account.Credentials.from_service_account_info(info, scopes=scopes)
    except Exception as e:
        raise ValueError(f"Failed to load service account: {e}")

def get_docs_client():
    creds = get_google_credentials(scopes=["https://www.googleapis.com/auth/documents"])
    return build('docs', 'v1', credentials=creds)

def get_sheets_data(sheet_id, tab_name):
    creds = get_google_credentials(scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    client = gspread.authorize(creds)
    sheet = client.open_by_key(sheet_id).worksheet(tab_name)
    return sheet.get_all_records()

@app.route("/multi", methods=["POST"])
def generate_multiple_rfp_sections():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Missing JSON payload"}), 400

    sheet_id = data.get("sheet_id")
    tab_name = data.get("tab_name")
    doc_id = data.get("doc_id")

    if not all([sheet_id, tab_name, doc_id]):
        return jsonify({"error": "Missing required fields"}), 400

    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        return jsonify({"error": "OPENAI_API_KEY not set"}), 500

    # Debug environment loading
    print("✅ GOOGLE_SERVICE_ACCOUNT exists:", bool(os.getenv("GOOGLE_SERVICE_ACCOUNT")))
    print("✅ OPENAI_API_KEY exists:", bool(openai_api_key))
    print("✅ FAISS index exists:", os.path.exists("faiss_index"))

    try:
        rows = get_sheets_data(sheet_id, tab_name)
    except Exception as e:
        return jsonify({"error": f"Failed to read sheet: {e}"}), 500

    try:
        embeddings = OpenAIEmbeddings(openai_api_key=openai_api_key)
        if not os.path.exists("faiss_index"):
            return jsonify({"error": "FAISS index folder not found"}), 500
        vectorstore = FAISS.load_local("faiss_index", embeddings, allow_dangerous_deserialization=True)
    except Exception as e:
        return jsonify({"error": f"Failed to load FAISS index: {e}"}), 500

    try:
        client = OpenAI(api_key=openai_api_key)
        docs_client = get_docs_client()
        doc = docs_client.documents().get(documentId=doc_id).execute()
        end_index = doc['body']['content'][-1]['endIndex'] - 1
    except Exception as e:
        return jsonify({"error": f"Failed to load or write to Google Doc: {e}"}), 500

    requests = []
    for row in rows:
        requirement = row.get("Requirement")
        base_response = row.get("Response")
        if not requirement or not base_response:
            continue

        try:
            docs = vectorstore.similarity_search(requirement, k=3)
        except Exception as e:
            return jsonify({"error": f"Vector search failed: {e}"}), 500

        context = "\n---\n".join([doc.page_content for doc in docs])

        prompt = f"""
You are a professional RFP proposal writer working for Fever, a global leader in ticketed experiences.
Your goal is to craft a compelling, polished section of an RFP response based on:
1. The RFP requirement below
2. Fever's base feature response
3. Additional supporting context pulled from internal documentation

Section: {requirement}

Base Response:
{base_response}

Supporting context:
{context}

Write a clear, narrative, persuasive section. Start with a bold, clear heading derived from the requirement. Do not use markdown or hashtags. This heading should summarize the requirement. Then follow with the full response text.
"""

        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4
            )
            text = response.choices[0].message.content.strip()
        except Exception as e:
            return jsonify({"error": f"OpenAI call failed: {e}"}), 500

        # Split heading and body
        lines = text.split("\n", 1)
        heading = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""

        requests.append({
            'insertText': {
                'location': {'index': end_index},
                'text': f"\n"
            }
        })
        end_index += 1

        requests.append({
            "insertText": {
                "location": {"index": end_index},
                "text": heading + "\n"
            }
        })
        requests.append({
            "updateParagraphStyle": {
                "range": {
                    "startIndex": end_index,
                    "endIndex": end_index + len(heading) + 1
                },
                "paragraphStyle": {
                    "namedStyleType": "HEADING_2"
                },
                "fields": "namedStyleType"
            }
        })
        end_index += len(heading) + 1

        requests.append({
            "insertText": {
                "location": {"index": end_index},
                "text": body + "\n\n"
            }
        })
        end_index += len(body) + 2

    if not requests:
        return jsonify({"error": "No valid rows to process"}), 400

    try:
        docs_client.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
    except Exception as e:
        return jsonify({"error": f"Failed to update Google Doc: {e}"}), 500

    return jsonify({"status": "ok", "message": "All sections inserted"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
