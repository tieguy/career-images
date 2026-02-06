"""
commons.py - Fetch file data from Wikimedia Commons categories

Uses the MediaWiki API to list files in Commons categories and retrieve
thumbnail URLs and basic metadata.
"""

import re
import requests
from urllib.parse import quote

HEADERS = {
    'User-Agent': 'WikipediaCareerDiversityTool/1.0 (https://github.com/tieguy/wikipedia-career-images)'
}

API_URL = "https://commons.wikimedia.org/w/api.php"


def fetch_category_members(category: str, limit: int = 50, continue_from: str = None) -> dict:
    """
    Fetch files and subcategories from a Commons category.

    Args:
        category: Commons category name (without "Category:" prefix)
        limit: Maximum number of files to return per page (max 50)
        continue_from: Continuation token for pagination

    Returns:
        {
            'files': [{title, thumb_url, image_url, description_url, description}],
            'subcategories': [{title, name, url}],
            'category': str,
            'category_url': str,
            'continue_from': str or None (for next page),
        }
    """
    # Fetch files using generator (gets imageinfo in one call)
    file_params = {
        'action': 'query',
        'generator': 'categorymembers',
        'gcmtitle': f'Category:{category}',
        'gcmtype': 'file',
        'gcmlimit': min(limit, 50),
        'gcmsort': 'timestamp',
        'gcmdir': 'desc',
        'prop': 'imageinfo',
        'iiprop': 'url|extmetadata|size',
        'iiurlwidth': 300,
        'format': 'json',
    }
    if continue_from:
        file_params['gcmcontinue'] = continue_from

    try:
        response = requests.get(API_URL, params=file_params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        return {
            'files': [], 'subcategories': [], 'category': category,
            'category_url': _category_url(category), 'error': str(e),
        }

    files = _parse_file_pages(data.get('query', {}).get('pages', {}))

    # Extract continuation token
    next_continue = None
    if 'continue' in data:
        next_continue = data['continue'].get('gcmcontinue')

    return {
        'files': files,
        'category': category,
        'category_url': _category_url(category),
        'continue_from': next_continue,
    }


def fetch_subcategories(category: str) -> list[dict]:
    """
    Fetch subcategories of a Commons category.

    Returns list of:
        {
            'title': str (full title with Category: prefix),
            'name': str (display name without prefix),
            'url': str (link to Commons category page),
        }
    """
    params = {
        'action': 'query',
        'list': 'categorymembers',
        'cmtitle': f'Category:{category}',
        'cmtype': 'subcat',
        'cmlimit': 100,
        'format': 'json',
    }

    try:
        response = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        return []

    subcats = []
    for member in data.get('query', {}).get('categorymembers', []):
        title = member.get('title', '')
        name = title.replace('Category:', '')
        subcats.append({
            'title': title,
            'name': name,
            'url': f'https://commons.wikimedia.org/wiki/{quote(title.replace(" ", "_"))}',
        })

    return subcats


def fetch_category_info(category: str) -> dict:
    """
    Fetch basic info about a Commons category (number of files and subcategories).

    Returns:
        {
            'category': str,
            'files': int,
            'subcategories': int,
            'pages': int,
        }
    """
    params = {
        'action': 'query',
        'titles': f'Category:{category}',
        'prop': 'categoryinfo',
        'format': 'json',
    }

    try:
        response = requests.get(API_URL, params=params, headers=HEADERS, timeout=15)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        return {'category': category, 'files': 0, 'subcategories': 0, 'pages': 0}

    pages = data.get('query', {}).get('pages', {})
    for page in pages.values():
        info = page.get('categoryinfo', {})
        return {
            'category': category,
            'files': info.get('files', 0),
            'subcategories': info.get('subcats', 0),
            'pages': info.get('pages', 0),
        }

    return {'category': category, 'files': 0, 'subcategories': 0, 'pages': 0}


# Keep the old name working for existing code
def fetch_category_files(category: str, limit: int = 50) -> dict:
    """Fetch files from a Commons category. Wrapper for backwards compatibility."""
    result = fetch_category_members(category, limit=limit)
    return result


def _parse_file_pages(pages: dict) -> list[dict]:
    """Parse file pages from a MediaWiki API response."""
    files = []
    for page_id, page in pages.items():
        if int(page_id) < 0:
            continue

        imageinfo = page.get('imageinfo', [{}])[0]
        if not imageinfo.get('url'):
            continue

        metadata = imageinfo.get('extmetadata', {})
        description = metadata.get('ImageDescription', {}).get('value', '')
        if description:
            description = re.sub(r'<[^>]+>', '', description)[:500]

        files.append({
            'title': page.get('title', ''),
            'thumb_url': imageinfo.get('thumburl', imageinfo.get('url')),
            'image_url': imageinfo.get('url'),
            'description_url': imageinfo.get('descriptionurl', ''),
            'description': description,
        })

    return files


def _category_url(category: str) -> str:
    """Build a Commons category URL."""
    return f'https://commons.wikimedia.org/wiki/Category:{quote(category.replace(" ", "_"))}'
