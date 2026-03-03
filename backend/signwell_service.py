"""
SignWell E-Signature Integration Service
API Docs: https://developers.signwell.com/reference
Simple key-based auth: X-Api-Key header
"""

import os
import base64
import requests
from typing import List, Dict, Optional


API_BASE = "https://www.signwell.com/api/v1"


class SignWellService:
    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self.api_key = os.getenv("SIGNWELL_API_KEY", "")
        self.headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
            "accept": "application/json",
        }
        print(f"🔧 SignWell Service initialized")
        print(f"   API Key: {self.api_key[:12]}..." if self.api_key else "   ⚠️ No API Key set")

    # ─────────────────────────────────────────
    # CONNECTION TEST
    # ─────────────────────────────────────────

    def test_connection(self) -> Dict:
        """Test API key by calling /me endpoint."""
        try:
            r = requests.get(f"{API_BASE}/me", headers=self.headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                user = data.get("user", {})
                account = data.get("account", {})
                print(f"✅ SignWell connected: {user.get('email')}")
                return {
                    "success": True,
                    "email": user.get("email"),
                    "name": user.get("name"),
                    "plan": account.get("plan_tier"),
                }
            return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ─────────────────────────────────────────
    # SEND DOCUMENTS FOR SIGNING
    # ─────────────────────────────────────────

    def send_documents(
        self,
        file_paths: List[str],
        signer_name: str,
        signer_email: str,
        document_name: str,
        message: str = "",
        subject: str = "",
        test_mode: bool = False,
    ) -> Dict:
        """
        Create and immediately send a document package for signing.

        Args:
            file_paths: List of absolute paths to PDF files
            signer_name: Recipient full name
            signer_email: Recipient email address
            document_name: Name shown in SignWell and email subject
            message: Optional email body message
            subject: Optional email subject override
            test_mode: If True, uses test mode (free, not legally binding)

        Returns:
            Dict with document_id, status, signing_url
        """
        # Build files list (base64 encode each PDF)
        files = []
        for path in file_paths:
            filename = os.path.basename(path)
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            files.append({"name": filename, "file_base64": b64})

        payload = {
            "test_mode": test_mode,
            "name": document_name,
            "subject": subject or f"Please sign: {document_name}",
            "message": message or f"Please review and sign the attached documents.",
            "files": files,
            "recipients": [
                {
                    "id": 1,
                    "name": signer_name,
                    "email": signer_email,
                    "role": "signer",
                }
            ],
            # Adds a signature page automatically — no field placement needed
            "with_signature_page": True,
            "draft": False,
            "reminders": True,
        }

        print(f"📤 SignWell: sending {len(files)} file(s) to {signer_email}")
        r = requests.post(f"{API_BASE}/documents/", headers=self.headers, json=payload, timeout=30)

        if r.status_code in (200, 201):
            data = r.json()
            doc_id = data.get("id")
            print(f"✅ SignWell document created: {doc_id}")
            # Store in DB if supabase is available
            self._store_request(doc_id, document_name, signer_name, signer_email, data)
            return {
                "success": True,
                "document_id": doc_id,
                "status": data.get("status"),
                "name": data.get("name"),
            }

        print(f"❌ SignWell error {r.status_code}: {r.text[:500]}")
        return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}

    # ─────────────────────────────────────────
    # SEND WITH PLACED FIELDS (SignWell Signer UI)
    # ─────────────────────────────────────────

    def send_with_fields(
        self,
        file_paths: List[str],
        signers: List[Dict],
        fields: List[Dict],
        document_name: str,
        message: str = "",
        test_mode: bool = False,
    ) -> Dict:
        """
        Send documents with specific signature fields placed by the signer UI.

        Args:
            file_paths: Ordered list of PDF file paths
            signers: [{"recipient_id": 1, "name": "...", "email": "..."}]
            fields: [{"type": "signature", "page": 1, "x": 100, "y": 200,
                      "width": 150, "height": 30, "recipient_id": 1}]
                    page is 1-indexed and global across all files.
        """
        # Build base64 file list and track page counts per file
        files = []
        page_counts = []
        for path in file_paths:
            filename = os.path.basename(path)
            with open(path, "rb") as f:
                raw = f.read()
            b64 = base64.b64encode(raw).decode("utf-8")
            files.append({"name": filename, "file_base64": b64})
            # Count PDF pages using pypdf if available
            try:
                from pypdf import PdfReader
                import io
                reader = PdfReader(io.BytesIO(raw))
                page_counts.append(len(reader.pages))
            except Exception:
                page_counts.append(1)

        # Build recipients — ids must be strings to match field recipient_ids
        recipients = []
        for s in signers:
            recipients.append({
                "id": str(int(s.get("recipient_id", 1))),
                "name": s.get("name", ""),
                "email": s.get("email", ""),
                "role": "signer",
            })

        # Partition fields into per-file arrays using cumulative page offsets
        cumulative = [0]
        for cnt in page_counts:
            cumulative.append(cumulative[-1] + cnt)

        # Valid SignWell field types
        VALID_TYPES = {'signature', 'initials', 'date_signed', 'text', 'name', 'checkbox', 'dropdown'}
        # Fields that require the signer to act (should be marked required)
        REQUIRED_TYPES = {'signature', 'initials'}

        fields_per_file = [[] for _ in file_paths]
        for field in fields:
            global_page = int(field.get("page") or 1)
            for idx in range(len(file_paths)):
                if cumulative[idx] < global_page <= cumulative[idx + 1]:
                    local_page = global_page - cumulative[idx]
                    # Strip FIRST then fallback — handles None, "", and whitespace-only strings
                    field_type = ((field.get("type") or "").strip()) or "text"
                    if field_type not in VALID_TYPES:
                        print(f"⚠️  Unknown field type '{field_type}', defaulting to 'text'")
                        field_type = "text"
                    built = {
                        "type": field_type,
                        "page": local_page,
                        "x": int(field.get("x") or 0),
                        "y": int(field.get("y") or 0),
                        "width": int(field.get("width") or 150),
                        "height": int(field.get("height") or 30),
                        "recipient_id": str(int(field.get("recipient_id") or 1)),
                        "required": field_type in REQUIRED_TYPES,
                    }
                    fields_per_file[idx].append(built)
                    break

        # Final safety pass — remove any field missing or blank type (SignWell returns 422 otherwise)
        for fi, flist in enumerate(fields_per_file):
            cleaned = []
            for fld in flist:
                t = (fld.get("type") or "").strip()
                if not t:
                    print(f"⚠️  Dropping field in file {fi+1} with blank type: {fld}")
                    continue
                fld["type"] = t  # ensure stripped value
                cleaned.append(fld)
            fields_per_file[fi] = cleaned

        has_fields = any(len(ff) > 0 for ff in fields_per_file)
        payload = {
            "test_mode": test_mode,
            "name": document_name,
            "subject": f"Please sign: {document_name}",
            "message": message or "Please review and sign the attached documents.",
            "files": files,
            "recipients": recipients,
            "draft": False,
            "reminders": True,
        }
        if has_fields:
            payload["fields"] = fields_per_file
        else:
            payload["with_signature_page"] = True

        total_placed = sum(len(ff) for ff in fields_per_file)
        print(f"📤 SignWell (fields): {len(files)} file(s), {len(fields)} raw field(s) → {total_placed} placed, {len(signers)} signer(s)")
        for fi, ff in enumerate(fields_per_file):
            for fld in ff:
                print(f"    file_{fi+1}: type={fld.get('type')!r} page={fld.get('page')} recipient={fld.get('recipient_id')}")
        r = requests.post(f"{API_BASE}/documents/", headers=self.headers, json=payload, timeout=30)

        if r.status_code in (200, 201):
            data = r.json()
            doc_id = data.get("id")
            print(f"✅ SignWell document with fields: {doc_id}")
            primary = signers[0] if signers else {}
            self._store_request(doc_id, document_name, primary.get("name", ""), primary.get("email", ""), data)
            return {
                "success": True,
                "document_id": doc_id,
                "status": data.get("status"),
                "name": data.get("name"),
            }

        print(f"❌ SignWell fields error {r.status_code}: {r.text[:500]}")
        return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}

    # ─────────────────────────────────────────
    # DOCUMENT STATUS
    # ─────────────────────────────────────────

    def get_document(self, document_id: str) -> Dict:
        """Get document status and details."""
        try:
            r = requests.get(f"{API_BASE}/documents/{document_id}", headers=self.headers, timeout=10)
            if r.status_code == 200:
                return {"success": True, "document": r.json()}
            return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ─────────────────────────────────────────
    # REMINDER
    # ─────────────────────────────────────────

    def send_reminder(self, document_id: str) -> Dict:
        """Send a signing reminder to pending recipients."""
        try:
            r = requests.post(f"{API_BASE}/documents/{document_id}/remind", headers=self.headers, timeout=10)
            if r.status_code in (200, 201, 204):
                return {"success": True, "message": "Reminder sent"}
            return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ─────────────────────────────────────────
    # DELETE / CANCEL
    # ─────────────────────────────────────────

    def delete_document(self, document_id: str) -> Dict:
        """Delete / cancel a document."""
        try:
            r = requests.delete(f"{API_BASE}/documents/{document_id}", headers=self.headers, timeout=10)
            if r.status_code in (200, 204):
                return {"success": True}
            return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ─────────────────────────────────────────
    # COMPLETED PDF
    # ─────────────────────────────────────────

    def get_completed_documents(self, document_id: str) -> Dict:
        """
        Get completed document metadata including per-file names and page counts.
        Returns: {success, files: [{name, pages_number}], document: {...}}
        """
        try:
            r = requests.get(
                f"{API_BASE}/documents/{document_id}",
                headers=self.headers, timeout=15,
            )
            if r.status_code != 200:
                return {"success": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
            doc = r.json()
            files = []
            for f in doc.get("files", []):
                files.append({
                    "name": f.get("name", ""),
                    "pages_number": int(f.get("pages_number", 1)),
                })
            return {"success": True, "files": files, "document": doc}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def download_completed_pdf(self, document_id: str) -> Optional[bytes]:
        """Download the completed (signed) PDF. Returns bytes or None."""
        try:
            r = requests.get(
                f"{API_BASE}/documents/{document_id}/completed_pdf",
                headers={k: v for k, v in self.headers.items() if k != "Content-Type"},
                timeout=30,
            )
            if r.status_code == 200:
                return r.content
            print(f"❌ Download failed: {r.status_code} {r.text[:200]}")
            return None
        except Exception as e:
            print(f"❌ Download error: {e}")
            return None

    # ─────────────────────────────────────────
    # WEBHOOK HANDLER
    # ─────────────────────────────────────────

    def process_webhook(self, payload: Dict) -> Dict:
        """Process an incoming SignWell webhook event."""
        event_type = payload.get("event", {}).get("type", "")
        doc_data = payload.get("document", {})
        doc_id = doc_data.get("id")

        print(f"📥 SignWell webhook: {event_type} | doc: {doc_id}")

        if event_type == "document_completed":
            self._handle_completed(doc_id, doc_data)
        elif event_type == "document_declined":
            self._handle_declined(doc_id, doc_data)

        return {"received": True, "event": event_type}

    def _handle_completed(self, doc_id, doc_data):
        if not self.supabase or not doc_id:
            return
        try:
            self.supabase.table("zoho_sign_requests").update(
                {"status": "completed"}
            ).eq("request_id", doc_id).execute()
            print(f"✅ DB updated: document {doc_id} completed")
        except Exception as e:
            print(f"⚠️ DB update failed: {e}")

    def _handle_declined(self, doc_id, doc_data):
        if not self.supabase or not doc_id:
            return
        try:
            self.supabase.table("zoho_sign_requests").update(
                {"status": "declined"}
            ).eq("request_id", doc_id).execute()
        except Exception as e:
            print(f"⚠️ DB update failed: {e}")

    # ─────────────────────────────────────────
    # DB TRACKING
    # ─────────────────────────────────────────

    def _store_request(self, doc_id, doc_name, signer_name, signer_email, raw_data,
                        lead_id=None, client_name=None, category=None):
        if not self.supabase or not doc_id:
            return
        safe_client = (client_name or 'unknown').replace('/', '_').replace(' ', '_')
        cat = category or 'general'
        bucket_folder = f"{safe_client}/{cat}"
        # Normalize SignWell status → pending / completed / declined
        sw_status = (raw_data.get("status") or "awaiting_signatures").lower()
        STATUS_MAP = {
            "awaiting_signatures": "pending", "draft": "pending",
            "completed": "completed", "declined": "declined",
            "voided": "declined", "expired": "declined",
        }
        norm_status = STATUS_MAP.get(sw_status, "pending")
        try:
            self.supabase.table("zoho_sign_requests").insert({
                "request_id": doc_id,
                "document_name": doc_name,
                "recipient_name": signer_name,
                "recipient_email": signer_email,
                "status": norm_status,
                "bucket_folder": bucket_folder,
                "lead_id": lead_id,
                "client_name": client_name,
                "category": category,
            }).execute()
            print(f"✅ zoho_sign_requests row saved for {doc_id[:16]}")
        except Exception as e:
            print(f"⚠️ DB store failed: {e}")
