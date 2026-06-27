"""
Google Drive Integration for Scribd Downloader
Upload downloaded PDFs to Google Drive automatically.

Setup:
1. Go to https://console.cloud.google.com/
2. Create/select project → Enable "Google Drive API"
3. Create OAuth2 credentials (Desktop app type)
4. Download credentials.json → place in app directory
5. On first use, authorize via browser or paste auth code
"""

import os
import json
import logging
import mimetypes
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CREDENTIALS_PATH = os.environ.get("GDRIVE_CREDENTIALS", "credentials.json")
TOKEN_PATH = os.environ.get("GDRIVE_TOKEN", "gdrive_token.json")
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_service = None


def _get_service():
    """Get or create Google Drive API service."""
    global _service
    if _service:
        return _service

    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        raise ImportError(
            "Google Drive packages not installed. Run:\n"
            "pip install google-api-python-client google-auth-oauthlib"
        )

    creds = None

    # Load existing token
    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except Exception as e:
            logger.warning(f"Failed to load token: {e}")

    # Refresh or get new credentials
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if not os.path.exists(CREDENTIALS_PATH):
            raise FileNotFoundError(
                f"credentials.json not found at {CREDENTIALS_PATH}.\n"
                "Download from Google Cloud Console → APIs → Credentials → OAuth 2.0"
            )
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
        creds = flow.run_local_server(port=0, open_browser=False)

        # Save token for next run
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        logger.info("Google Drive authorized successfully")

    _service = build("drive", "v3", credentials=creds)
    return _service


def is_configured() -> bool:
    """Check if Google Drive is configured (credentials exist)."""
    return os.path.exists(CREDENTIALS_PATH)


def is_authorized() -> bool:
    """Check if Google Drive is authorized (valid token exists)."""
    if not os.path.exists(TOKEN_PATH):
        return False
    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        return creds.valid or (creds.expired and creds.refresh_token)
    except Exception:
        return False


def get_auth_url() -> Optional[str]:
    """Get OAuth2 authorization URL for manual flow."""
    if not os.path.exists(CREDENTIALS_PATH):
        return None
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
        flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        auth_url, _ = flow.authorization_url(prompt="consent")
        return auth_url
    except Exception as e:
        logger.error(f"Failed to generate auth URL: {e}")
        return None


def authorize_with_code(code: str) -> dict:
    """Complete authorization with the code from Google."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
        flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        flow.fetch_token(code=code)
        creds = flow.credentials
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        global _service
        _service = None  # Reset to rebuild with new creds
        return {"success": True, "message": "Google Drive đã kết nối thành công!"}
    except Exception as e:
        return {"success": False, "message": f"Lỗi: {str(e)}"}


def get_or_create_folder(folder_name: str, parent_id: str = None) -> str:
    """Get existing folder or create new one. Returns folder ID."""
    service = _get_service()

    # Search for existing folder
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = service.files().list(
        q=query, spaces="drive", fields="files(id, name)"
    ).execute()

    files = results.get("files", [])
    if files:
        return files[0]["id"]

    # Create new folder
    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = service.files().create(body=metadata, fields="id").execute()
    logger.info(f"Created folder '{folder_name}': {folder['id']}")
    return folder["id"]


def upload_file(file_path: str, folder_id: str = None,
                custom_name: str = None) -> dict:
    """
    Upload a file to Google Drive.
    Returns: {success, file_id, web_link, name}
    """
    service = _get_service()

    path = Path(file_path)
    if not path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    name = custom_name or path.name
    mime_type = mimetypes.guess_type(file_path)[0] or "application/pdf"

    metadata = {"name": name}
    if folder_id:
        metadata["parents"] = [folder_id]

    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(file_path, mimetype=mime_type, resumable=True)

    try:
        file_obj = service.files().create(
            body=metadata,
            media_body=media,
            fields="id, name, webViewLink, webContentLink, size"
        ).execute()

        result = {
            "success": True,
            "file_id": file_obj["id"],
            "name": file_obj["name"],
            "web_link": file_obj.get("webViewLink", ""),
            "download_link": file_obj.get("webContentLink", ""),
            "size": int(file_obj.get("size", 0)),
        }
        logger.info(f"Uploaded '{name}' → {result['file_id']}")
        return result

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        return {"success": False, "error": str(e)}


def upload_scribd_pdf(file_path: str, doc_title: str,
                      doc_id: str, base_folder: str = "Scribd Downloads") -> dict:
    """
    Upload a Scribd PDF to Google Drive in organized folder structure.
    Creates: Scribd Downloads / <YYYY-MM> / <title>.pdf
    """
    try:
        from datetime import datetime
        month_folder = datetime.now().strftime("%Y-%m")

        base_id = get_or_create_folder(base_folder)
        month_id = get_or_create_folder(month_folder, parent_id=base_id)

        # Clean filename
        safe_title = "".join(c for c in doc_title if c.isalnum() or c in " ._-()").strip()
        if not safe_title:
            safe_title = f"scribd_{doc_id}"
        filename = f"{safe_title}.pdf"

        result = upload_file(file_path, folder_id=month_id, custom_name=filename)
        result["folder"] = f"{base_folder}/{month_folder}"
        return result

    except Exception as e:
        logger.error(f"Failed to upload Scribd PDF: {e}")
        return {"success": False, "error": str(e)}


def list_uploaded_files(folder_name: str = "Scribd Downloads",
                        limit: int = 50) -> list[dict]:
    """List files in the Scribd Downloads folder."""
    try:
        service = _get_service()
        # Find folder
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(q=query, spaces="drive", fields="files(id)").execute()
        folders = results.get("files", [])
        if not folders:
            return []

        # List all files recursively
        all_files = []
        _list_recursive(service, folders[0]["id"], all_files, limit)
        return all_files[:limit]

    except Exception as e:
        logger.error(f"Failed to list files: {e}")
        return []


def _list_recursive(service, folder_id: str, result: list, limit: int):
    """Recursively list files in folder and subfolders."""
    if len(result) >= limit:
        return
    query = f"'{folder_id}' in parents and trashed=false"
    items = service.files().list(
        q=query, spaces="drive",
        fields="files(id, name, mimeType, size, createdTime, webViewLink)",
        orderBy="createdTime desc"
    ).execute().get("files", [])

    for item in items:
        if item["mimeType"] == "application/vnd.google-apps.folder":
            _list_recursive(service, item["id"], result, limit)
        else:
            result.append({
                "id": item["id"],
                "name": item["name"],
                "size": int(item.get("size", 0)),
                "created": item.get("createdTime", ""),
                "link": item.get("webViewLink", ""),
            })


def get_status() -> dict:
    """Get Google Drive connection status."""
    configured = is_configured()
    authorized = is_authorized() if configured else False
    return {
        "configured": configured,
        "authorized": authorized,
        "credentials_path": CREDENTIALS_PATH,
        "token_path": TOKEN_PATH,
    }


def disconnect():
    """Remove authorization (delete token)."""
    global _service
    _service = None
    if os.path.exists(TOKEN_PATH):
        os.remove(TOKEN_PATH)
        return True
    return False
