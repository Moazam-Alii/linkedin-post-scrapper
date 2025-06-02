# main.py
import os
import asyncio
from flask import Flask, request, render_template, redirect, url_for, flash, session
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from openai import OpenAI
from utils import clean_post_text, generate_post_heading, extract_post_images, save_and_upload_images
from utils import generate_post_insights
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
import subprocess

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
load_dotenv()

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

def insert_multiple_posts(doc_id, posts, creds):
    """
    posts: list of dicts with keys: heading, body, image_urls, failed_links
    Insert all posts in one batch update.
    """
    try:
        service = build('docs', 'v1', credentials=creds)
        doc = service.documents().get(documentId=doc_id).execute()
        content = doc.get('body').get('content')
        end_index = content[-1].get('endIndex', 1)
        requests = []
        current_index = end_index - 1

        for post in posts:
            heading = post['heading']
            body = post['body']
            image_urls = post['image_urls']
            failed_links = post['failed_links']

            # Generate insights for this post
            insights_text = generate_post_insights(client, body)
            insights_lines = insights_text.splitlines()

            # Insert heading
            requests.append({'insertText': {'location': {'index': current_index}, 'text': heading + '\n\n'}})
            requests.append({'updateParagraphStyle': {
                'range': {'startIndex': current_index, 'endIndex': current_index + len(heading)},
                'paragraphStyle': {'namedStyleType': 'HEADING_1'},
                'fields': 'namedStyleType'
            }})
            current_index += len(heading) + 2

            # Insert insights lines
            for line in insights_lines:
                if line.strip():
                    line_text = line.strip() + '\n'
                    requests.append({'insertText': {'location': {'index': current_index}, 'text': line_text}})
                    current_index += len(line_text)

            # Spacing after insights
            requests.append({'insertText': {'location': {'index': current_index}, 'text': '\n'}})
            current_index += 1

            # Insert body text
            requests.append({'insertText': {'location': {'index': current_index}, 'text': body + '\n\n'}})
            current_index += len(body) + 2

            # Insert images
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

            # Insert failed image links as fallback
            for link in failed_links:
                requests.append({'insertText': {'location': {'index': current_index}, 'text': f"{link}\n"}})
                current_index += len(link) + 1

            # Extra spacing between posts
            requests.append({'insertText': {'location': {'index': current_index}, 'text': '\n\n'}})
            current_index += 2

        # Execute batch update
        service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()
        return True, "✅ All posts inserted successfully."
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
        num_urls = request.form.get('num_urls')

        if not doc_id:
            flash("Please enter your Google Doc ID.", "error")
            return redirect(url_for('start'))

        try:
            num_urls = int(num_urls)
            if num_urls < 1 or num_urls > 20:
                flash("Number of URLs must be between 1 and 20.", "error")
                return redirect(url_for('start'))
        except:
            flash("Please enter a valid number for number of URLs.", "error")
            return redirect(url_for('start'))

        session['doc_id'] = doc_id
        session['num_urls'] = num_urls
        return redirect(url_for('add_posts'))

    return render_template('start.html')

@app.route('/add', methods=['GET', 'POST'])
def add_posts():
    if 'doc_id' not in session or 'num_urls' not in session:
        flash("Please enter your Google Doc ID and number of URLs first.", "error")
        return redirect(url_for('start'))

    num_urls = session['num_urls']
    doc_id = session['doc_id']

    if request.method == 'POST':
        linkedin_urls = request.form.getlist('linkedin_urls')
        if not linkedin_urls or len(linkedin_urls) != num_urls:
            flash(f"Please enter exactly {num_urls} LinkedIn URLs.", "error")
            return redirect(url_for('add_posts'))

        creds = get_credentials()
        if not creds:
            flash("Please authorize with Google.", "error")
            return redirect(url_for('authorize'))

        posts = []
        try:
            for url in linkedin_urls:
                raw_text, image_urls = asyncio.run(scrape_post_content(url))
                cleaned = clean_post_text(client, raw_text)
                heading = generate_post_heading(client, cleaned)
                uploaded_images, failed_images = save_and_upload_images(
                    image_urls, folder="images", prefix=url.split("/")[-1], creds=creds
                )
                posts.append({
                    "heading": heading,
                    "body": cleaned,
                    "image_urls": uploaded_images,
                    "failed_links": failed_images
                })

            success, message = insert_multiple_posts(doc_id, posts, creds)
            flash(message, "success" if success else "error")
        except Exception as e:
            flash(f"❌ Error: {e}", "error")

        return redirect(url_for('add_posts'))

    return render_template('add_posts.html', num_urls=num_urls)

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
