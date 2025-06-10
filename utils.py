import os
import asyncio
from flask import Flask, request, render_template, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from openai import OpenAI
from utils import (
    scrape_post_content,
    clean_post_text,
    generate_post_heading,
    generate_post_insights,
    save_and_upload_images
)

app = Flask(__name__)

SCOPES = ["https://www.googleapis.com/auth/documents", "https://www.googleapis.com/auth/drive"]
SERVICE_ACCOUNT_FILE = "service_account.json"
creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)

drive_service = build("drive", "v3", credentials=creds)
docs_service = build("docs", "v1", credentials=creds)
openai_client = OpenAI()


@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")


@app.route("/scrape", methods=["POST"])
def scrape():
    urls = request.form.get("urls").splitlines()
    doc_id = request.form.get("doc_id")
    new_doc_title = request.form.get("new_doc_title")

    if not urls:
        return "No URLs provided", 400

    if new_doc_title:
        document = docs_service.documents().create(body={"title": new_doc_title}).execute()
        doc_id = document.get("documentId")
    elif not doc_id:
        return "Document ID or new title is required", 400

    try:
        results = asyncio.run(process_urls_sequentially(urls, doc_id))
        return jsonify({"doc_id": doc_id, "results": results})
    except Exception as e:
        return str(e), 500


async def process_urls_sequentially(urls, doc_id):
    results = []
    for url in urls:
        try:
            content, image_urls = await scrape_post_content(url)
            cleaned = clean_post_text(openai_client, content)
            heading = generate_post_heading(openai_client, cleaned)
            insights = generate_post_insights(openai_client, cleaned)

            drive_urls, failed = save_and_upload_images(
                image_urls, folder="downloads", prefix=heading, creds=creds
            )

            requests = []
            requests.append({"insertText": {"location": {"index": 1}, "text": f"\nHeading: {heading}\n"}})
            requests.append({"insertText": {"location": {"index": 1}, "text": f"Post: {cleaned}\n"}})
            requests.append({"insertText": {"location": {"index": 1}, "text": f"Insights:\n{insights}\n"}})

            for image_url in drive_urls:
                requests.append({
                    "insertInlineImage": {
                        "location": {"index": 1},
                        "uri": image_url,
                        "objectSize": {"height": {"magnitude": 300, "unit": "PT"}, "width": {"magnitude": 300, "unit": "PT"}},
                    }
                })

            docs_service.documents().batchUpdate(documentId=doc_id, body={"requests": list(reversed(requests))}).execute()
            results.append({"url": url, "status": "success", "images": len(drive_urls), "failed_images": len(failed)})

        except Exception as e:
            results.append({"url": url, "status": "error", "error": str(e)})

    return results


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
