import os
import urllib.request
from urllib.parse import urljoin
import asyncio
from playwright.async_api import async_playwright

def clean_post_text(client, text):
    unwanted = [
        "followers", "reactions", "comments", "reply", "student at",
        "like", "1h", "2h", "3h", "minutes ago", "contact us"
    ]
    prompt = f"""
You are a smart content cleaner. Given the following LinkedIn post content, extract only the useful text that seems like the main body of the post and try not to add the comments of the post also dont add the bio of profiles. Ignore these keywords and metadata: {', '.join(unwanted)}.

--- Raw Text ---
{text}

--- Cleaned Content ---
"""
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return response.choices[0].message.content.strip()

def generate_post_heading(client, cleaned_text):
    prompt = f"""
You are an assistant that generates engaging, professional titles and intros for LinkedIn posts.
Based on the following post content, generate a short and relevant heading.

--- Post Content ---
{cleaned_text}

--- Title ---
"""
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4
    )
    return response.choices[0].message.content.strip()

def generate_post_insights(client, cleaned_text):
    prompt = f"""
You are a professional writing assistant. Analyze the following LinkedIn post and extract the key insights or takeaways.

Respond with 3 to 5 clear, concise one-liner insights. Each insight should be a standalone line, like bullet points, and should avoid repeating the post verbatim.

--- LinkedIn Post ---
{cleaned_text}

--- Insights ---
"""
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5
    )
    return response.choices[0].message.content.strip()

def save_and_upload_images(image_urls, folder, prefix, creds):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    if not os.path.exists(folder):
        os.makedirs(folder)

    drive_service = build('drive', 'v3', credentials=creds)

    drive_file_urls = []
    failed_urls = []

    for i, url in enumerate(image_urls):
        try:
            ext = os.path.splitext(url)[1].split("?")[0]
            if ext.lower() not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                ext = ".jpg"

            safe_prefix = "".join(c for c in prefix if c.isalnum() or c in (' ', '_')).rstrip()
            local_path = os.path.join(folder, f"{safe_prefix}_{i+1}{ext}")
            urllib.request.urlretrieve(url, local_path)

            file_metadata = {'name': os.path.basename(local_path), 'parents': []}
            media = MediaFileUpload(local_path, resumable=True)
            uploaded_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

            file_id = uploaded_file.get('id')
            drive_service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields='id'
            ).execute()

            drive_url = f"https://drive.google.com/uc?id={file_id}"
            drive_file_urls.append(drive_url)
        except Exception as e:
            print(f"Failed to process {url}: {e}")
            failed_urls.append(url)

    return drive_file_urls, failed_urls

async def extract_post_images(page, base_url):
    image_urls = []
    for _ in range(3):
        await page.mouse.wheel(0, 500)
        await page.wait_for_timeout(1000)

    images = await page.locator("article img").all()
    for img in images:
        src = await img.get_attribute("src") or ""
        alt = (await img.get_attribute("alt") or "").lower()
        class_name = (await img.get_attribute("class") or "").lower()
        keywords = ["profile", "avatar", "banner", "emoji", "icon", "logo"]
        if not src or src.startswith("data:image"):
            continue
        if any(k in src.lower() for k in keywords) or any(k in alt for k in keywords) or any(k in class_name for k in keywords):
            continue
        if "media.licdn.com" in src and src not in image_urls:
            image_urls.append(urljoin(base_url, src))

    picture_sources = await page.locator("article picture source").all()
    for source in picture_sources:
        srcset = await source.get_attribute("srcset") or ""
        for src_part in srcset.split(","):
            url = src_part.strip().split(" ")[0]
            if url and "media.licdn.com" in url and url not in image_urls:
                if not any(k in url.lower() for k in ["profile", "avatar", "banner", "emoji", "icon", "logo"]):
                    image_urls.append(urljoin(base_url, url))

    return image_urls

async def scrape_post_content(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-gpu", "--no-sandbox"])
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
        except:
            content = await page.inner_text("body")

        image_urls = await extract_post_images(page, url)
        await browser.close()
        return content, image_urls
