# main.py
import os
import asyncio
from flask import Flask, request, render_template, redirect, url_for, flash, session
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from openai import OpenAI
from utils import clean_post_text, generate_post_heading, extract_post_images, save_and_upload_images

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
load_dotenv()

import subprocess

# Install Chromium browser on app startup (only the first run will download)
subprocess.run(["playwright", "install", "chromium"])


app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your-secret-key")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/documents'
]

CLIENT_SECRETS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'

def get_credentials():
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if creds and creds.valid:
            return creds
    return None

def insert_text_and_images(doc_id, heading, body, image_urls, failed_links, creds):
    try:
        service = build('docs', 'v1', credentials=creds)
        doc = service.documents().get(documentId=doc_id).execute()
        content = doc.get('body').get('content')
        end_index = content[-1].get('endIndex', 1)

        requests = []

        current_index = end_index - 1

        # Insert heading
        requests.append({'insertText': {'location': {'index': current_index}, 'text': heading + '\n\n'}})
        requests.append({'updateParagraphStyle': {
            'range': {'startIndex': current_index, 'endIndex': current_index + len(heading)},
            'paragraphStyle': {'namedStyleType': 'HEADING_1'},
            'fields': 'namedStyleType'
        }})
        current_index += len(heading) + 2

        # Insert cleaned text
        requests.append({'insertText': {'location': {'index': current_index}, 'text': body + '\n\n'}})
        current_index += len(body) + 2

        # Insert inline images
        for img_url in image_urls:
            requests.append({
                'insertInlineImage': {
                    'location': {'index': current_index},
                    'uri': img_url,
                    'objectSize': {
                        'height': {'magnitude': 300, 'unit': 'PT'},
                        'width': {'magnitude': 300, 'unit': 'PT'}
                    }
                }
            })
            current_index += 1
            requests.append({'insertText': {'location': {'index': current_index}, 'text': '\n'}})
            current_index += 1

        # Fallback links
        for link in failed_links:
            requests.append({'insertText': {'location': {'index': current_index}, 'text': f"{link}\n"}})
            current_index += len(link) + 1

        service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()
        return True, "✅ Content inserted with images and links."
    except Exception as e:
        return False, f"❌ Google Docs Error: {e}"

async def scrape_post_content(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, timeout=60000)
        await page.wait_for_selector("article", timeout=15000)
        await page.wait_for_timeout(3000)

        prev_height = None
        while True:
            curr_height = await page.evaluate("document.body.scrollHeight")
            if prev_height == curr_height:
                break
            prev_height = curr_height
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)

        try:
            content = await page.inner_text("article")
        except Exception:
            content = await page.inner_text("body")

        image_urls = await extract_post_images(page, url)
        await browser.close()
        return content, image_urls

@app.route('/', methods=['GET', 'POST'])
def start():
    if request.method == 'POST':
        doc_id = request.form.get('google_doc_id')
        if not doc_id:
            flash("Please enter your Google Doc ID.", "error")
            return redirect(url_for('start'))
        session['doc_id'] = doc_id  # ✅ Save in session
        return redirect(url_for('add_post'))

    return render_template('start.html')


@app.route('/add', methods=['GET', 'POST'])
def add_post():
    if 'doc_id' not in session:
        flash("Please enter your Google Doc ID first.", "error")
        return redirect(url_for('start'))

    if request.method == 'POST':
        linkedin_url = request.form.get('linkedin_url')
        if not linkedin_url:
            flash("Please enter a LinkedIn URL.", "error")
            return redirect(url_for('add_post'))

        creds = get_credentials()
        if not creds:
            flash("Please authorize with Google.", "error")
            return redirect(url_for('authorize'))

        try:
            raw_text, image_urls = asyncio.run(scrape_post_content(linkedin_url))
            cleaned = clean_post_text(client, raw_text)
            heading = generate_post_heading(client, cleaned)

            uploaded_images, failed_images = save_and_upload_images(
                image_urls, folder="images", prefix=linkedin_url.split("/")[-1], creds=creds
            )

            success, message = insert_text_and_images(
                session['doc_id'], heading, cleaned, uploaded_images, failed_images, creds
            )

            flash("✅ Post added successfully!" if success else f"⚠️ {message}", "success" if success else "error")
        except Exception as e:
            flash(f"❌ Error: {e}", "error")

        return redirect(url_for('add_post'))

    return render_template('add_post.html')

@app.route('/authorize')
def authorize():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri="https://scrapelinked.onrender.com/oauth2callback"
    )
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=state,
        redirect_uri="https://scrapelinked.onrender.com/oauth2callback"
    )
    flow.fetch_token(authorization_response=request.url)

    creds = flow.credentials
    with open(TOKEN_FILE, 'w') as token:
        token.write(creds.to_json())

    flash("✅ Google API credentials saved successfully.", "success")
    return redirect(url_for('start'))

 

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
