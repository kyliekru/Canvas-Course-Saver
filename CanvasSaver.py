import os
import re
import requests
import json
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------
CANVAS_BASE_URL = "https://[YOUR_INSTITUTION_HERE].instructure.com/api/v1/"
ACCESS_TOKEN = "[YOUR_ACCESS_TOKEN]"
COURSE_ID = "[COURSE_ID]"
DOWNLOAD_DIR = "[NAME_FOR_COURSE_FILE]"

STYLE_BLOCK = """
<style>
  body {
    font-family: Arial, sans-serif;
    margin: 20px;
    line-height: 1.6;
    background: #f9f9f9;
  }
  h2 {
    margin-top: 1.5rem;
    color: #003366;
  }
  hr {
    margin: 1.5rem 0;
    border: 0;
    height: 1px;
    background: #ccc;
  }
  a {
    color: #007c92;
    text-decoration: none;
  }
  a:hover {
    text-decoration: underline;
  }
  .embedded-video {
    margin: 1rem 0;
    background: #fff;
    padding: 10px;
    border: 1px solid #ccc;
  }
</style>
"""

# ---------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------
def sanitize_filename(name: str) -> str:
    """Replace or remove characters that are not allowed in filenames on most OSes."""
    return re.sub(r'[\\/*?:"<>|]', '_', name)

def canvas_request(endpoint, params=None, method="GET"):
    """
    Fetches *all* pages of results from a Canvas endpoint that returns a paginated list.
    If the response is a list, we accumulate them. If it's a single object, we return it directly.
    """
    if params is None:
        params = {}

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    base_url = urljoin(CANVAS_BASE_URL, endpoint)
    results = []
    url = base_url

    while url:
        print(f"Fetching URL: {url}")
        response = requests.request(method, url, headers=headers, params=params)
        response.raise_for_status()

        data = response.json()
        if isinstance(data, list):
            # Accumulate results
            results.extend(data)
        else:
            # If it's a single object, return immediately
            return data

        # Check for 'next' link in response headers
        links = response.links
        next_link = links.get('next', {}).get('url')

        # Only use params on the first request
        params = None
        url = next_link

    return results

def download_file(file_url, save_path):
    """
    Download a file from a direct link (file_url) and save to save_path.
    """
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    with requests.get(file_url, headers=headers, stream=True) as r:
        r.raise_for_status()
        with open(save_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

def fix_youtube_embeds(html_body):
    """
    Looks for <iframe> with 'embed/' in src and ensures it has
    a proper 'https://www.youtube.com/embed/...' URL (no double slashes).
    """
    soup = BeautifulSoup(html_body, 'html.parser')
    iframes = soup.find_all('iframe')
    for iframe in iframes:
        old_src = iframe.get('src', '')
        print(f"  [Iframe] Found src={old_src}")

        if 'embed/' in old_src:
            temp = old_src.replace('embed/', '/embed/')

            # Remove leading double slash if present (e.g. '//www.youtube.com')
            while temp.startswith('//'):
                temp = temp[1:]  # drop one slash

            # If it starts with "/www.youtube.com/embed"
            if temp.startswith('/www.youtube.com/embed'):
                temp = 'https://' + temp[1:]

            elif temp.startswith('/embed/'):
                temp = 'https://www.youtube.com' + temp

            if not temp.startswith('http'):
                embed_parts = temp.split('/embed/')
                if len(embed_parts) == 2:
                    temp = 'https://www.youtube.com/embed/' + embed_parts[1]

            iframe['src'] = temp
            print(f"    -> Final embed src = {temp}")

    return str(soup)

def extract_file_links_from_html(html_body):
    """
    Parse a page's HTML to find links that reference '/files/' in Canvas.
    """
    soup = BeautifulSoup(html_body, 'html.parser')
    links = []
    for link_tag in soup.find_all('a', href=True):
        href = link_tag['href']
        if '/files/' in href:
            links.append(href)
    return links

def get_file_info_from_link(href):
    """
    If href looks like '/courses/12345/files/67890/download', extract 67890
    """
    parts = href.split('/files/')
    if len(parts) < 2:
        return None
    right_side = parts[1]
    non_digit_split = right_side.split('/', 1)[0].split('?', 1)[0]
    if non_digit_split.isdigit():
        return non_digit_split
    return None


# ---------------------------------------------------------
# Modules
# ---------------------------------------------------------
def get_course_modules(course_id):
    endpoint = f"courses/{course_id}/modules"
    return canvas_request(endpoint)

def get_module_items(course_id, module_id):
    endpoint = f"courses/{course_id}/modules/{module_id}/items"
    return canvas_request(endpoint)

def download_modules(course_id, modules_list, base_dir):
    for module in modules_list:
        module_id = module["id"]
        module_name_raw = module["name"]
        folder_name = f"{module_id}_{module_name_raw}"
        sanitized_module_name = sanitize_filename(folder_name)
        print(f"\n=== Module ID: {module_id} | Name: {module_name_raw} ===")

        module_dir = os.path.join(base_dir, sanitized_module_name)
        os.makedirs(module_dir, exist_ok=True)

        items = get_module_items(course_id, module_id)
        combined_pages_html = []

        for item in items:
            item_type = item["type"]
            title = item["title"]

            if item_type == "File":
                file_id = item["content_id"]
                file_info = canvas_request(f"courses/{course_id}/files/{file_id}")
                file_url = file_info["url"]
                filename = file_info["filename"]
                print(f"  [File] Downloading {filename}")
                save_path = os.path.join(module_dir, filename)
                download_file(file_url, save_path)

            elif item_type == "Page":
                page_url_slug = item["page_url"]
                print(f"  [Page] Downloading page: {title} (slug: {page_url_slug})")
                endpoint = f"courses/{course_id}/pages/{page_url_slug}"
                try:
                    page_data = canvas_request(endpoint)
                except requests.HTTPError as e:
                    if e.response.status_code == 404:
                        print(f"    Page disabled or not accessible: {title}")
                        continue
                    else:
                        raise

                page_html = page_data.get("body", "")
                page_title = page_data.get("title", "Untitled")

                page_html_fixed = fix_youtube_embeds(page_html)
                combined_pages_html.append(f"<h2>{page_title}</h2>\n{page_html_fixed}\n<hr>")

                # Check embedded file links
                file_links = extract_file_links_from_html(page_html_fixed)
                for href in file_links:
                    embedded_file_id = get_file_info_from_link(href)
                    if not embedded_file_id:
                        continue
                    try:
                        embedded_file_info = canvas_request(f"files/{embedded_file_id}")
                        embedded_file_url = embedded_file_info["url"]
                        embedded_filename = embedded_file_info["filename"]
                        print(f"    [Embedded File] Downloading {embedded_filename}")
                        embedded_save_path = os.path.join(module_dir, embedded_filename)
                        download_file(embedded_file_url, embedded_save_path)
                    except requests.HTTPError as e2:
                        print(f"    Error downloading embedded file with ID {embedded_file_id}: {e2}")

            elif item_type == "ExternalUrl":
                external_url = item["external_url"]
                print(f"  [ExternalUrl] {title} -> {external_url}")
                combined_pages_html.append(
                    f"<h2>{title}</h2>\n<p>External link: <a href=\"{external_url}\">{external_url}</a></p>\n<hr>"
                )

            elif item_type == "ExternalTool":
                print(f"  [ExternalTool] {title}")
                combined_pages_html.append(
                    f"<h2>{title}</h2>\n<p>External Tool (LTI)</p>\n<hr>"
                )

            else:
                print(f"  [Unhandled] Item type: {item_type}")

        # Write combined HTML for the module
        if combined_pages_html:
            combined_html_content = (
                "<html>\n<head>\n"
                f"{STYLE_BLOCK}\n"
                "</head>\n<body>\n"
                + "\n".join(combined_pages_html) +
                "\n</body>\n</html>"
            )
            combined_filename = os.path.join(module_dir, f"{sanitized_module_name}_combined_pages.html")
            with open(combined_filename, "w", encoding="utf-8") as f:
                f.write(combined_html_content)
            print(f"  [Module Combined HTML] -> {combined_filename}")


# ---------------------------------------------------------
# Pages (Outside Modules)
# ---------------------------------------------------------
def safe_get_all_pages(course_id):
    """
    Attempt to list course pages.
    If the course doesn't have /pages enabled (404),
    just return an empty list or skip.
    """
    endpoint = f"courses/{course_id}/pages"
    try:
        data = canvas_request(endpoint)
        # data could be a dict if there's only one page or something unusual,
        # or a list if multiple pages.
        if isinstance(data, dict):
            # Wrap single object in a list
            return [data]
        return data
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            print("Pages are disabled or not accessible for this course. Skipping pages.")
            return []
        else:
            raise

def download_all_pages(course_id, pages_list, base_dir):
    """
    Download each page individually as .html.
    """
    if not pages_list:
        print("No pages found or pages disabled.")
        return

    pages_dir = os.path.join(base_dir, "all_pages")
    os.makedirs(pages_dir, exist_ok=True)

    for p in pages_list:
        # p might be incomplete; verify 'url' and 'title' exist
        page_url = p.get("url")
        page_title_raw = p.get("title", "Untitled Page")
        if not page_url:
            print("  [Page] Missing 'url' field, skipping.")
            continue

        page_title = sanitize_filename(page_title_raw)

        # get the full page
        endpoint = f"courses/{course_id}/pages/{page_url}"
        try:
            page_data = canvas_request(endpoint)
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                print(f"    Page slug '{page_url}' not found or pages disabled.")
                continue
            else:
                raise

        page_html = page_data.get("body", "")
        page_html_fixed = fix_youtube_embeds(page_html)

        path = os.path.join(pages_dir, f"{page_title}.html")
        print(f"  [Page] Saving {page_title}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "<html><head>" + STYLE_BLOCK + "</head><body>"
                + f"<h1>{page_title}</h1>\n{page_html_fixed}"
                + "</body></html>"
            )


# ---------------------------------------------------------
# Assignments
# ---------------------------------------------------------
def get_assignments(course_id):
    endpoint = f"courses/{course_id}/assignments"
    data = canvas_request(endpoint)
    # data could be a single object or a list
    if isinstance(data, dict):
        return [data]
    return data

def download_assignments(course_id, assignments_list, base_dir):
    if not assignments_list:
        print("No assignments found.")
        return

    assignments_dir = os.path.join(base_dir, "assignments")
    os.makedirs(assignments_dir, exist_ok=True)

    for a in assignments_list:
        title = sanitize_filename(a.get("name", "Untitled"))
        desc = a.get("description", "")
        assignment_id = a["id"]
        print(f"  [Assignment] {title} (ID {assignment_id})")

        # Save the assignment description as HTML
        path = os.path.join(assignments_dir, f"{title}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                f"<html><head>{STYLE_BLOCK}</head><body>\n"
                f"<h1>{title}</h1>\n{desc}\n"
                "</body></html>"
            )


# ---------------------------------------------------------
# Front Page (Home Page)
# ---------------------------------------------------------
def get_front_page(course_id):
    endpoint = f"courses/{course_id}/front_page"
    try:
        data = canvas_request(endpoint)
        return data
    except requests.HTTPError:
        print("No front page or not accessible.")
        return None

def save_front_page(front_page_data, base_dir):
    if not front_page_data:
        return
    front_title = sanitize_filename(front_page_data.get("title", "HomePage"))
    front_html = front_page_data.get("body", "")

    out_path = os.path.join(base_dir, f"{front_title}_frontpage.html")
    print(f"  [Front Page] -> {out_path}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(
            "<html><head>" + STYLE_BLOCK + "</head><body>"
            + f"<h1>{front_title}</h1>\n{front_html}"
            + "</body></html>"
        )

# ---------------------------------------------------------
# Files
# ---------------------------------------------------------
def get_all_files(course_id):
    endpoint = f"courses/{course_id}/files"
    try:
        data = canvas_request(endpoint)
    except requests.HTTPError as e:
        if e.response.status_code == 403:
            print("Files area is restricted or you do not have permissions. Skipping files.")
            return []
        else:
            raise
    # data might be single object or list
    if isinstance(data, dict):
        return [data]
    return data

def download_all_files(course_id, files_list, base_dir):
    if not files_list:
        print("No files found.")
        return

    files_dir = os.path.join(base_dir, "all_files")
    os.makedirs(files_dir, exist_ok=True)

    for fdata in files_list:
        fid = fdata["id"]
        fname = fdata["filename"]
        print(f"  [File] {fname}")
        # Get direct download URL
        try:
            file_json = canvas_request(f"files/{fid}")
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                print(f"    File {fname} not found or no permission.")
                continue
            else:
                raise

        file_url = file_json["url"]
        download_file(file_url, os.path.join(files_dir, fname))

# ---------------------------------------------------------
# Main
# ---------------------------------------------------------
def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    print(f"=== Downloading Course {COURSE_ID} ===")

    # 1) Modules
    print("\n---> MODULES")
    modules = get_course_modules(COURSE_ID)
    if isinstance(modules, dict):
        # single object => no modules
        print("No modules found.")
    elif modules:
        download_modules(COURSE_ID, modules, DOWNLOAD_DIR)
    else:
        print("No modules found.")

    # 2) Pages
    print("\n---> ALL PAGES (outside modules)")
    pages_list = safe_get_all_pages(COURSE_ID)
    download_all_pages(COURSE_ID, pages_list, DOWNLOAD_DIR)

    # 3) Assignments
    print("\n---> ASSIGNMENTS")
    assignments_list = get_assignments(COURSE_ID)
    download_assignments(COURSE_ID, assignments_list, DOWNLOAD_DIR)

    # 4) Front Page
    print("\n---> FRONT PAGE / HOME PAGE")
    front_page_data = get_front_page(COURSE_ID)
    if front_page_data:
        save_front_page(front_page_data, DOWNLOAD_DIR)

    # 5) All Files
    print("\n---> ALL FILES")
    files_list = get_all_files(COURSE_ID)
    download_all_files(COURSE_ID, files_list, DOWNLOAD_DIR)

    print("\nAll done!")

if __name__ == "__main__":
    main()
