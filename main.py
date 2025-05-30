import os
import asyncio
from flask import Flask, request, render_template, redirect, url_for, flash, session
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from openai import OpenAI
from flask import session

from utils import clean_post_text, save_images, extract_post_images, generate_post_heading

# Google API imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# Allow OAuthlib to run on HTTP (local dev only, remove for production)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "your-secret-key")

client = OpenAI(api_key=OPENAI_API_KEY)

# Google OAuth2 setup
SCOPES = ['https://www.googleapis.com/auth/documents']
CLIENT_SECRETS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'

def get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    # If no valid credentials, return None (user needs to authenticate)
    if not creds or not creds.valid:
        return None
    return creds

def save_credentials(creds):
    with open(TOKEN_FILE, 'w') as token:
        token.write(creds.to_json())

def insert_text_to_doc(doc_id, heading, body, image_paths):
    creds = get_credentials()
    if not creds:
        return False, "Google credentials not found or expired. Please authenticate."

    try:
        service = build('docs', 'v1', credentials=creds)
        doc = service.documents().get(documentId=doc_id).execute()
        end_index = doc.get('body').get('content')[-1].get('endIndex', 1)

        requests = []

        # Insert heading
        requests.append({
            'insertText': {
                'location': {'index': end_index - 1},
                'text': heading + '\n\n'
            }
        })
        requests.append({
            'updateParagraphStyle': {
                'range': {
                    'startIndex': end_index - 1,
                    'endIndex': end_index - 1 + len(heading) + 2
                },
                'paragraphStyle': {
                    'namedStyleType': 'HEADING_1'
                },
                'fields': 'namedStyleType'
            }
        })

        # Insert body text after heading
        requests.append({
            'insertText': {
                'location': {'index': end_index - 1 + len(heading) + 2},
                'text': body + '\n\n'
            }
        })

        # Insert image URLs as clickable links
        if image_paths:
            images_text = "\nImages:\n"
            for path in image_paths:
                images_text += f"{path}\n"
            requests.append({
                'insertText': {
                    'location': {'index': end_index - 1 + len(heading) + 2 + len(body) + 2},
                    'text': images_text
                }
            })

        service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute()
        return True, "Content inserted successfully."
    except Exception as e:
        return False, f"Error inserting into Google Doc: {e}"

async def scrape_post_content(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, timeout=60000)
        await page.wait_for_selector("article", timeout=15000)
        await page.wait_for_timeout(3000)

        previous_height = None
        while True:
            current_height = await page.evaluate("document.body.scrollHeight")
            if previous_height == current_height:
                break
            previous_height = current_height
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
def index():
    if request.method == 'POST':
        linkedin_url = request.form.get('linkedin_url')
        google_doc_id = request.form.get('google_doc_id')

        if not linkedin_url or not google_doc_id:
            flash("Please provide both LinkedIn URL and Google Doc ID.", "error")
            return redirect(url_for('index'))

        # Run the async scraping and processing
        raw_text, image_urls = asyncio.run(scrape_post_content(linkedin_url))
        cleaned = clean_post_text(client, raw_text)
        heading = generate_post_heading(client, cleaned)
        saved_paths = save_images(image_urls, folder="images", prefix=linkedin_url.split("/")[-1])

        # Insert content to Google Doc
        success, message = insert_text_to_doc(google_doc_id, heading, cleaned, saved_paths)
        if success:
            flash("LinkedIn post content successfully inserted into Google Doc.", "success")
        else:
            flash(message, "error")

        return redirect(url_for('index'))

    return render_template('index.html')

@app.route('/authorize')
def authorize():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri="http://localhost:5000/oauth2callback"
    )
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true'
        
    )
    session['state'] = state  # Save state for OAuth callback verification
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session.get('state')
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=state,
        redirect_uri="http://localhost:5000/oauth2callback"
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    save_credentials(creds)
    flash("Google API credentials saved successfully. You can now insert content.", "success")
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(debug=True)
