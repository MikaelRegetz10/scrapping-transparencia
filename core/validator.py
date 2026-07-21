from typing import Any, Dict
import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


def check_url_status(url: str, timeout: int = 10) -> Dict[str, Any]:
    """Testa a URL via HEAD e faz fallback para GET se o servidor recusar HEAD."""
    result = {
        "status_code": None,
        "is_active": False,
        "content_type": None,
        "content_length_kb": None,
        "error": None,
    }

    if not url or not url.startswith("http"):
        result["error"] = "URL inválida"
        return result

    try:
        response = requests.head(
            url, headers=DEFAULT_HEADERS, allow_redirects=True, timeout=timeout
        )

        # Trata casos onde o servidor não aceita HEAD (405 / 403 / 501)
        if response.status_code in [403, 405, 501]:
            response = requests.get(
                url,
                headers=DEFAULT_HEADERS,
                allow_redirects=True,
                stream=True,
                timeout=timeout,
            )

        result["status_code"] = response.status_code
        result["is_active"] = 200 <= response.status_code < 400
        result["content_type"] = response.headers.get("Content-Type", "")

        content_length = response.headers.get("Content-Length")
        if content_length and content_length.isdigit():
            result["content_length_kb"] = round(int(content_length) / 1024, 2)

    except requests.exceptions.Timeout:
        result["error"] = "Timeout"
    except requests.exceptions.RequestException as e:
        result["error"] = str(e)

    return result