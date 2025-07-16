
import functions_framework
import openai
import os
from googleapiclient.discovery import build
from google.oauth2 import service_account
from langchain.vectorstores import FAISS
from langchain.embeddings import OpenAIEmbeddings

def get_docs_client():
    creds = service_account.Credentials.from_service_account_file(
        "service_account.json",
        scopes=["https://www.googleapis.com/auth/documents"]
    )
    return build('docs', 'v1', credentials=creds)

@functions_framework.http
def generate_rfp_response(request):
    data = request.get_json(silent=True)
    if not data:
        return {"error": "Missing JSON payload"}, 400

    requirement = data.get("requirement")
    base_response = data.get("response")
    doc_id = data.get("doc_id")

    if not all([requirement, base_response, doc_id]):
        return {"error": "Missing required fields"}, 400

    openai.api_key = os.environ.get("OPENAI_API_KEY")
    if not openai.api_key:
        return {"error": "OPENAI_API_KEY not set in environment"}, 500

    embeddings = OpenAIEmbeddings(openai_api_key=openai.api_key)

    # Local path for development or cloud-based FAISS index
    index_path = "faiss_index"
    vectorstore = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)

    docs = vectorstore.similarity_search(requirement, k=3)
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

Write a clear, narrative, persuasive section starting with a bold heading (no asterisks). Use a confident and informative tone suitable for large-scale partners.
"""

    result = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4
    )

    text = result.choices[0].message.content.strip()

    # Insert at end of document
    docs_client = get_docs_client()
    doc = docs_client.documents().get(documentId=doc_id).execute()
    end_index = doc['body']['content'][-1]['endIndex'] - 1

    docs_client.documents().batchUpdate(
        documentId=doc_id,
        body={'requests': [{'insertText': {'location': {'index': end_index}, 'text': f"\n\n{text}\n"}}]}
    ).execute()

    return {
        "status": "ok",
        "message": "RFP section inserted",
        "text": text
    }, 200
