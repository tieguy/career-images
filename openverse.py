"""
openverse.py - Search Openverse for CC-licensed images

API docs: https://api.openverse.org/v1/
"""

import requests
from typing import Optional

# Wikipedia-compatible licenses
COMPATIBLE_LICENSES = "pdm,cc0,by,by-sa"

HEADERS = {
    'User-Agent': 'WikipediaCareerDiversityTool/1.0 (https://github.com/tieguy/wikipedia-career-images)'
}


def search_images(query: str, page: int = 1, page_size: int = 20) -> dict:
    """
    Search Openverse for images matching query.

    Args:
        query: Search terms (e.g., "female engineer", "male nurse")
        page: Page number (1-indexed)
        page_size: Results per page (max 50)

    Returns:
        {
            'results': [
                {
                    'id': str,
                    'title': str,
                    'thumbnail': str (URL),
                    'url': str (full image URL),
                    'foreign_landing_url': str (source page),
                    'license': str,
                    'license_url': str,
                    'creator': str,
                    'source': str (e.g., 'flickr', 'wikimedia'),
                }
            ],
            'result_count': int,
            'page_count': int,
        }
    """
    url = "https://api.openverse.org/v1/images/"
    params = {
        'q': query,
        'license': COMPATIBLE_LICENSES,
        'page': page,
        'page_size': min(page_size, 50),
    }

    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        return {'results': [], 'result_count': 0, 'page_count': 0, 'error': str(e)}

    results = []
    for item in data.get('results', []):
        results.append({
            'id': item.get('id'),
            'title': item.get('title', 'Untitled'),
            'thumbnail': item.get('thumbnail'),
            'url': item.get('url'),
            'foreign_landing_url': item.get('foreign_landing_url'),
            'license': item.get('license'),
            'license_url': item.get('license_url'),
            'creator': item.get('creator', 'Unknown'),
            'source': item.get('source', 'Unknown'),
        })

    return {
        'results': results,
        'result_count': data.get('result_count', 0),
        'page_count': data.get('page_count', 0),
    }


def get_image_detail(image_id: str) -> Optional[dict]:
    """
    Get detailed info about a specific Openverse image.

    Returns:
        {
            'id': str,
            'title': str,
            'url': str,
            'thumbnail': str,
            'foreign_landing_url': str,
            'license': str,
            'license_url': str,
            'license_version': str,
            'creator': str,
            'creator_url': str,
            'source': str,
            'attribution': str (pre-formatted attribution text),
        }
    """
    url = f"https://api.openverse.org/v1/images/{image_id}/"

    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        item = response.json()
    except requests.RequestException:
        return None

    return {
        'id': item.get('id'),
        'title': item.get('title', 'Untitled'),
        'url': item.get('url'),
        'thumbnail': item.get('thumbnail'),
        'foreign_landing_url': item.get('foreign_landing_url'),
        'license': item.get('license'),
        'license_url': item.get('license_url'),
        'license_version': item.get('license_version'),
        'creator': item.get('creator', 'Unknown'),
        'creator_url': item.get('creator_url'),
        'source': item.get('source', 'Unknown'),
        'attribution': item.get('attribution'),
    }


def generate_commons_upload_url(image: dict) -> str:
    """
    Generate a Commons Upload Wizard URL with pre-filled metadata.

    Note: Commons doesn't have direct URL import from arbitrary sources,
    but we can link to the upload wizard with some context.
    """
    # Commons Special:Upload doesn't support pre-filling from URL
    # Best we can do is link to upload wizard
    base_url = "https://commons.wikimedia.org/wiki/Special:UploadWizard"
    return base_url


def generate_attribution(image: dict) -> str:
    """Generate attribution text for an image."""
    if image.get('attribution'):
        return image['attribution']

    parts = []
    if image.get('title'):
        parts.append(f'"{image["title"]}"')
    if image.get('creator'):
        parts.append(f"by {image['creator']}")
    if image.get('license'):
        license_text = image['license'].upper()
        if image.get('license_version'):
            license_text += f" {image['license_version']}"
        parts.append(f"({license_text})")
    if image.get('source'):
        parts.append(f"via {image['source']}")

    return " ".join(parts)


def generate_wikitext(image: dict, filename: str, caption: str = "") -> str:
    """
    Generate wikitext for embedding an image in Wikipedia.

    Args:
        image: Openverse image dict
        filename: The filename it will have on Commons (without File: prefix)
        caption: Caption for the image
    """
    if not caption:
        caption = image.get('title', '')

    return f"[[File:{filename}|thumb|{caption}]]"


if __name__ == '__main__':
    # Test search
    print("Testing Openverse search for 'female engineer'...")
    results = search_images("female engineer", page_size=5)
    print(f"Found {results['result_count']} results")

    for img in results['results']:
        print(f"\n- {img['title']}")
        print(f"  License: {img['license']}")
        print(f"  Creator: {img['creator']}")
        print(f"  Source: {img['source']}")
        print(f"  Thumbnail: {img['thumbnail'][:60]}...")
