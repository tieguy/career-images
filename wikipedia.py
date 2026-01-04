"""
wikipedia.py - Fetch article content and images from Wikipedia API
"""

import requests
from urllib.parse import unquote

# Filter out these common template/icon images
IGNORED_IMAGE_PREFIXES = (
    'File:OOjs',
    'File:Ambox',
    'File:Question',
    'File:Symbol',
    'File:Wiki',
    'File:Commons',
    'File:Edit-',
    'File:Globe',
    'File:Folder',
    'File:Portal',
    'File:Flag',
    'File:Crystal',
    'File:Nuvola',
    'File:Gnome',
    'File:Padlock',
    'File:Lock-',
    'File:Semi-',
    'File:Text',
    'File:Splitsection',
    'File:Merge-',
    'File:Wikibooks',
    'File:Wikiquote',
    'File:Wikisource',
    'File:Wiktionary',
    'File:Wikinews',
    'File:Wikiversity',
    'File:Wikivoyage',
    'File:Wikidata',
    'File:Wikispecies',
)

HEADERS = {
    'User-Agent': 'WikipediaCareerDiversityTool/1.0 (https://github.com/tieguy/wikipedia-career-images)'
}


def extract_title_from_url(url: str) -> str:
    """Extract article title from Wikipedia URL"""
    if '/wiki/' in url:
        title = url.split('/wiki/')[-1]
        return unquote(title).replace('_', ' ')
    return ''


def fetch_article_content(title: str) -> dict:
    """
    Fetch article lede and main thumbnail.

    Returns:
        {
            'title': str,
            'lede': str,
            'thumbnail_url': str or None,
        }
    """
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        'action': 'query',
        'titles': title,
        'prop': 'pageimages|extracts',
        'exintro': True,
        'explaintext': True,
        'piprop': 'thumbnail',
        'pithumbsize': 400,
        'redirects': True,  # Follow redirects
        'format': 'json',
    }

    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        return {'title': title, 'lede': '', 'thumbnail_url': None, 'error': str(e)}

    pages = data.get('query', {}).get('pages', {})
    for page_id, page in pages.items():
        if page_id == '-1':  # Page not found
            return {'title': title, 'lede': '', 'thumbnail_url': None}

        return {
            'title': page.get('title', title),
            'lede': page.get('extract', ''),
            'thumbnail_url': page.get('thumbnail', {}).get('source'),
        }

    return {'title': title, 'lede': '', 'thumbnail_url': None}


def fetch_article_images(title: str) -> list[dict]:
    """
    Fetch all images from an article with their URLs and captions.

    Returns list of:
        {
            'image_url': str,
            'thumb_url': str,
            'caption': str,
            'title': str (file title),
        }
    """
    # Step 1: Get image titles from article
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        'action': 'query',
        'titles': title,
        'prop': 'images',
        'imlimit': 50,
        'redirects': True,  # Follow redirects
        'format': 'json',
    }

    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        return []

    # Extract image titles, filtering out templates
    image_titles = []
    pages = data.get('query', {}).get('pages', {})
    for page in pages.values():
        for img in page.get('images', []):
            img_title = img.get('title', '')
            if not any(img_title.startswith(prefix) for prefix in IGNORED_IMAGE_PREFIXES):
                # Only include actual image files
                if any(img_title.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp')):
                    image_titles.append(img_title)

    if not image_titles:
        return []

    # Step 2: Get image URLs for these titles
    params = {
        'action': 'query',
        'titles': '|'.join(image_titles[:20]),  # API limit
        'prop': 'imageinfo',
        'iiprop': 'url|extmetadata',
        'iiurlwidth': 400,  # Get thumbnail
        'format': 'json',
    }

    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        return []

    images = []
    pages = data.get('query', {}).get('pages', {})
    for page in pages.values():
        title = page.get('title', '')
        imageinfo = page.get('imageinfo', [{}])[0]

        if imageinfo.get('url'):
            # Extract caption from metadata if available
            metadata = imageinfo.get('extmetadata', {})
            caption = metadata.get('ImageDescription', {}).get('value', '')
            # Clean up HTML from caption
            if caption:
                import re
                caption = re.sub(r'<[^>]+>', '', caption)
                caption = caption[:500]  # Truncate long captions

            images.append({
                'title': title,
                'image_url': imageinfo.get('url'),
                'thumb_url': imageinfo.get('thumburl', imageinfo.get('url')),
                'caption': caption,
            })

    return images


def fetch_career_data(wikipedia_url: str) -> dict:
    """
    Fetch all data needed for reviewing a career.

    Returns:
        {
            'title': str,
            'lede': str,
            'thumbnail_url': str or None,
            'images': list[dict],
        }
    """
    title = extract_title_from_url(wikipedia_url)
    if not title:
        return {'title': '', 'lede': '', 'thumbnail_url': None, 'images': []}

    content = fetch_article_content(title)
    images = fetch_article_images(title)

    return {
        'title': content['title'],
        'lede': content['lede'],
        'thumbnail_url': content['thumbnail_url'],
        'images': images,
    }


if __name__ == '__main__':
    # Test with a few careers
    test_urls = [
        'https://en.wikipedia.org/wiki/Programmer',
        'https://en.wikipedia.org/wiki/Pope',
        'https://en.wikipedia.org/wiki/Nurse',
    ]

    for url in test_urls:
        print(f"\n{'='*60}")
        data = fetch_career_data(url)
        print(f"Title: {data['title']}")
        print(f"Lede: {data['lede'][:150]}..." if data['lede'] else "Lede: (none)")
        print(f"Thumbnail: {data['thumbnail_url']}" if data['thumbnail_url'] else "Thumbnail: (none)")
        print(f"Images: {len(data['images'])}")
        for img in data['images'][:5]:
            print(f"  - {img['title']}")
            print(f"    Thumb: {img['thumb_url'][:80]}...")
