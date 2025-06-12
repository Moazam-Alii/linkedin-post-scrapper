import os
import asyncio
from flask import Flask, request, render_template, redirect, url_for, flash, session
from dotenv import load_dotenv
from openai import OpenAI
from utils import (
    clean_post_text, generate_post_heading, extract_post_images,
    save_and_upload_images, generate_post_insights,
    scrape_post_content, process_one_by_one, insert_multiple_posts
)
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
import subprocess

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
load_dotenv()

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

def create_new_google_doc(creds, title="Scraped LinkedIn Posts"):
    try:
        service = build('docs', 'v1', credentials=creds)
        doc = service.documents().create(body={'title': title}).execute()
        return doc['documentId']
    except Exception as e:
        print(f"Error creating document: {e}")
        return None

@app.route('/', methods=['GET', 'POST'])
def start():
    if request.method == 'POST':
        doc_id = request.form.get('google_doc_id')
        create_new = request.form.get('create_new') == 'on'
        num_urls = request.form.get('num_urls')

        try:
            num_urls = int(num_urls)
            if num_urls < 1 or num_urls > 20:
                flash("Number of URLs must be between 1 and 20.", "error")
                return redirect(url_for('start'))
        except:
            flash("Please enter a valid number for number of URLs.", "error")
            return redirect(url_for('start'))

        creds = get_credentials()
        if not creds:
            flash("Please authorize with Google first.", "error")
            return redirect(url_for('authorize'))

        if create_new:
            doc_id = create_new_google_doc(creds)
            if not doc_id:
                flash("Failed to create a new Google Doc.", "error")
                return redirect(url_for('start'))

        if not doc_id:
            flash("Please provide or create a Google Doc.", "error")
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

    num_urls = int(session['num_urls'])
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

        try:
            posts = asyncio.run(process_one_by_one(linkedin_urls, creds, client))
            success, message = insert_multiple_posts(doc_id, posts, creds, client)
            if success:
                return redirect(f"https://docs.google.com/document/d/{doc_id}/edit")
            else:
                flash(message, "error")
        except Exception as e:
            flash(f"❌ Error: {e}", "error")

        return redirect(url_for('add_posts'))

    return render_template('add_post.html', num_urls=num_urls)

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
