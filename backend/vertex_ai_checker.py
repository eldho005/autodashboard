"""
Vertex AI Document Verification Service
Uses Google Gemini 1.5 Pro to validate and cross-verify insurance documents before signing
"""

import os
import json
import base64
from typing import List, Dict
from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel, Part
import vertexai


class DocumentVerificationService:
    """Service for AI-powered document verification using Vertex AI Gemini"""
    
    def __init__(self):
        # Set credentials if provided
        creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        if creds_path:
            # Make it absolute path relative to this file's directory
            if not os.path.isabs(creds_path):
                creds_path = os.path.join(os.path.dirname(__file__), creds_path)
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = creds_path
            print(f"📁 Using credentials: {creds_path}")
        
        # Try GOOGLE_CLOUD_PROJECT first (matches pdf_parser.py), fall back to GOOGLE_CLOUD_PROJECT_ID
        self.project_id = os.getenv('GOOGLE_CLOUD_PROJECT') or os.getenv('GOOGLE_CLOUD_PROJECT_ID')
        self.location = os.getenv('GOOGLE_CLOUD_LOCATION', 'us-central1')
        self.model_name = os.getenv('VERTEX_AI_MODEL', 'gemini-2.0-flash')
        
        # Initialize Vertex AI
        if self.project_id:
            print(f"🔧 Initializing Vertex AI...")
            print(f"   Project: {self.project_id}")
            print(f"   Location: {self.location}")
            print(f"   Model: {self.model_name}")
            vertexai.init(project=self.project_id, location=self.location)
            self.model = GenerativeModel(self.model_name)
            print(f"✅ Vertex AI initialized successfully!")
        else:
            self.model = None
            print("⚠️ GOOGLE_CLOUD_PROJECT not set - Vertex AI disabled")
    
    def verify_document_package(
        self, 
        pdf_files: List[str], 
        client_name: str = None,
        policy_type: str = None,
        document_names: List[str] = None
    ) -> Dict:
        """
        Verify a complete client document package using Gemini
        
        Args:
            pdf_files: List of PDF file paths to verify
            client_name: Expected client name for verification
            policy_type: 'auto', 'home', or 'both'
            document_names: List of original filenames (same order as pdf_files)
            
        Returns:
            Dict with verification results in structured JSON format
        """
        if not self.model:
            return {
                "error": "Vertex AI not configured. Please set GOOGLE_CLOUD_PROJECT in .env",
                "overall_status": "ERROR"
            }
        
        try:
            print(f"📄 Verifying {len(pdf_files)} documents for {client_name or 'client'}...")
            
            # Prepare document parts for Gemini
            parts = []
            
            # Build document list for the prompt
            doc_names_list = document_names if document_names else [os.path.basename(p) for p in pdf_files]
            
            # Add the comprehensive verification prompt with document names
            verification_prompt = self._get_verification_prompt(client_name, policy_type, doc_names_list)
            parts.append(Part.from_text(verification_prompt))
            
            # Add each PDF document with filename label
            for i, pdf_path in enumerate(pdf_files):
                if not os.path.exists(pdf_path):
                    print(f"⚠️ File not found: {pdf_path}")
                    continue
                
                # Get the document name for this file
                doc_name = doc_names_list[i] if i < len(doc_names_list) else os.path.basename(pdf_path)
                
                # Add a text label before each PDF so AI knows the filename
                parts.append(Part.from_text(f"\n--- DOCUMENT: {doc_name} ---\n"))
                
                with open(pdf_path, 'rb') as f:
                    pdf_data = f.read()
                    pdf_part = Part.from_data(
                        data=pdf_data,
                        mime_type='application/pdf'
                    )
                    parts.append(pdf_part)
                    print(f"  ✓ Added: {doc_name}")
            
            # Generate verification report
            print("🤖 Analyzing documents with Gemini...")
            response = self.model.generate_content(
                parts,
                generation_config={
                    "temperature": 0.1,  # Low temperature for consistent, factual analysis
                    "top_p": 0.95,
                    "top_k": 40,
                    "max_output_tokens": 8192,
                }
            )
            
            # Parse JSON response
            response_text = response.text.strip()
            
            # Remove markdown code fences if present
            if response_text.startswith('```json'):
                response_text = response_text[7:]
            if response_text.startswith('```'):
                response_text = response_text[3:]
            if response_text.endswith('```'):
                response_text = response_text[:-3]
            
            response_text = response_text.strip()
            
            result = json.loads(response_text)
            
            # Handle both old and new JSON structures
            if 'overall_status' in result:
                print(f"✅ Verification complete: {result['overall_status']}")
                print(f"   Ready to send: {result.get('ready_to_send', 'N/A')}")
                print(f"   Total Issues: {result.get('total_issues', 0)}")
                print(f"   Critical: {result.get('critical_issues', 0)}")
                print(f"   Warnings: {result.get('warnings', 0)}")
            elif 'client_file_summary' in result:
                # Old structure
                summary = result['client_file_summary']
                print(f"✅ Verification complete: {summary['overall_status']}")
                print(f"   Total Issues: {summary['total_issues']}")
                print(f"   Critical: {summary['critical_issues']}")
                print(f"   Warnings: {summary['warnings']}")
            
            return result
            
        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse JSON response: {str(e)}")
            print(f"Response text: {response_text[:500]}")
            return {
                "error": f"Failed to parse AI response: {str(e)}",
                "raw_response": response_text,
                "overall_status": "ERROR"
            }
        except Exception as e:
            print(f"❌ Error during document verification: {str(e)}")
            return {
                "error": str(e),
                "overall_status": "ERROR"
            }
    
    def _get_verification_prompt(self, client_name: str = None, policy_type: str = None, document_names: List[str] = None) -> str:
        """Generate the balanced verification prompt - thorough but not overwhelming"""
        
        context = ""
        if client_name:
            context += f"Expected client name: {client_name}\n"
        if policy_type:
            context += f"Policy type: {policy_type}\n"
        
        # Include the exact document filenames so AI uses them correctly
        if document_names:
            context += f"\n## DOCUMENT FILENAMES (use these EXACT names in your response):\n"
            for i, name in enumerate(document_names, 1):
                context += f"{i}. {name}\n"
            context += "\nIMPORTANT: When referencing documents in issues, use the EXACT filenames listed above.\n"
        
        prompt = f"""You are an insurance document auditor for KMI Brokers Inc.

{context}

Analyze these insurance documents and verify they are ready to send for signature.

## IMPORTANT: ALL SIGNATURES ARE DIGITAL

All signatures in these documents are **digital/electronic signatures** — NOT handwritten or wet ink signatures.
A digitally signed field will appear as one of the following:
- Typed name in a signature font or cursive style
- An electronic signature image or stamp
- A "Digitally signed by [Name]" or "e-Signed" annotation
- A filled checkbox or initials box with typed/printed characters
- A DocuSign, ZohoSign, or similar e-signature provider stamp

A signature field is considered **BLANK/MISSING** only if the field is empty, contains only underscores/blank lines, or has no content at all.
Do NOT flag a digital signature as missing just because it does not look like a handwritten ink signature.

## CRITICAL: LOCATION DETAILS REQUIRED

For EVERY issue, discrepancy, or missing item you find, you MUST provide:
1. **Document name** (exact filename)
2. **Page number** (which page in that document)
3. **Page section** (top/middle/bottom, and more specific location like "top right - header" or "bottom - signature block")
4. **Field name/label** (what the field is called on the form)
5. **Current value** (what you see, or "BLANK/MISSING" if empty)

Example good reporting:
- "Document: Auto_Quote.pdf, Page 2, Section: middle-left - vehicle information, Field: VIN, Value: 1HGBH41JXMN109186"
- "Document: OAF1.pdf, Page 3, Section: bottom - signature block, Field: Applicant Signature, Value: MISSING"

This detail is essential so staff can quickly locate and fix errors without searching through entire documents.

## CHECK THESE FIELDS (must match across all documents):

**Client Information:**
- client_name_primary (full name)
- client_name_secondary (co-applicant if any)
- primary_address (full street address + postal code)
- phone_number
- email_address
- date_of_birth_primary
- date_of_birth_secondary (if applicable)

**Policy Details (Auto):**
- auto_policy_number (or binder number)
- auto_insurer_name
- auto_effective_date
- auto_expiry_date
- auto_annual_premium
- vehicle_year
- vehicle_make
- vehicle_model
- vehicle_vin

**Policy Details (Home/Tenant/Condo):**
- home_policy_number (or binder number)
- home_insurer_name
- home_effective_date
- home_expiry_date
- home_annual_premium
- property_address (if different from mailing address)
- dwelling_value (for homeowners)

**Payment Information:**
- monthly_payment_amount
- number_of_installments
- preferred_billing_date

## CROSS-CHECK RULES:

**CRITICAL: For cross-document discrepancies, you MUST report BOTH documents with their conflicting values:**

Example of CORRECT cross-document reporting:
{{
  "issue": "Policy number mismatch between documents",
  "document_1": "Auto_Quote.pdf",
  "document_1_page": 1,
  "document_1_section": "top right - policy info",
  "document_1_field": "Policy Number",
  "document_1_value": "AUTO-123456",
  "document_2": "Auto_Application.pdf",
  "document_2_page": 1,
  "document_2_section": "top - header",
  "document_2_field": "Policy No",
  "document_2_value": "AUTO-789012",
  "severity": "FAIL",
  "action": "Verify correct policy number - documents show AUTO-123456 vs AUTO-789012"
}}

NEVER report a cross-document issue with only one document - ALWAYS show both documents that differ!

**FAIL if:**
- Client name is completely different (not just spelling) → Show BOTH documents with values
- Policy/binder number differs between documents → Show BOTH documents with values
- Effective date differs by more than 1 day → Show BOTH documents with values
- Premium differs by more than $100 → Show BOTH documents with values
- VIN doesn't match across documents → Show BOTH documents with values
- Missing critical policy information → Show document where it's missing

**WARN if:**
- Name has minor spelling variation (e.g., "John" vs "Jonathan") → Show BOTH documents with values
- Address abbreviation differs (e.g., "St" vs "Street") → Show BOTH documents with values
- Premium differs by $1-$100 → Show BOTH documents with values
- Phone format differs (e.g., spaces, dashes) → Show BOTH documents with values
- Insurer name variation (e.g., "Definity" vs "Economical" - same company) → Show BOTH documents with values

**IGNORE:**
- Different date formats (as long as same date)
- Different address capitalization
- Minor punctuation differences
- Trailing zeros in dollar amounts

## OUTPUT JSON:

{{
  "ready_to_send": true/false,
  "overall_status": "PASS | WARN | FAIL",
  "client_name": "string",
  "documents_analyzed": ["filename1.pdf", "filename2.pdf"],
  "total_issues": 0,
  "critical_issues": 0,
  "warnings": 0,
  
  "critical_issues_list": [
    {{
      "issue": "Missing applicant signature",
      "document": "OAF1_Application.pdf",
      "page": 3,
      "page_section": "bottom - signature block",
      "field_name": "Applicant 1 Signature",
      "severity": "FAIL",
      "action": "Client must sign page 3, bottom section in the applicant signature field"
    }},
    {{
      "issue": "Policy number mismatch between documents",
      "document_1": "Auto_Quote.pdf",
      "document_1_page": 1,
      "document_1_section": "top right - policy info",
      "document_1_field": "Policy Number",
      "document_1_value": "AUTO-123456",
      "document_2": "Auto_Application.pdf",
      "document_2_page": 1,
      "document_2_section": "top - header", 
      "document_2_field": "Policy No",
      "document_2_value": "AUTO-789012",
      "severity": "FAIL",
      "action": "Verify correct policy number with insurer - Quote shows AUTO-123456, Application shows AUTO-789012"
    }}
  ],
  
  "warnings_list": [
    {{
      "issue": "Name spelling variation detected",
      "document_1": "Quote.pdf",
      "document_1_page": 1,
      "document_1_section": "top - policyholder information",
      "document_1_value": "Jon Smith",
      "document_2": "Application.pdf",
      "document_2_page": 2,
      "document_2_section": "middle - applicant details",
      "document_2_value": "John Smith",
      "severity": "WARN",
      "action": "Verify correct spelling with client - check ID"
    }}
  ],
  
  "field_verification": {{
    "client_name_primary": {{
      "status": "PASS", 
      "value": "John Smith",
      "found_in": [
        {{"document": "Quote.pdf", "page": 1, "section": "top"}},
        {{"document": "Application.pdf", "page": 2, "section": "middle"}}
      ]
    }},
    "auto_policy_number": {{
      "status": "FAIL",
      "values": [
        {{"document": "Quote.pdf", "page": 1, "section": "top right - policy info", "value": "AUTO-123"}},
        {{"document": "Application.pdf", "page": 1, "section": "top - header", "value": "AUTO-456"}}
      ]
    }},
    "auto_effective_date": {{
      "status": "PASS", 
      "value": "2026-03-15",
      "found_in": [
        {{"document": "Quote.pdf", "page": 1, "section": "middle"}},
        {{"document": "Application.pdf", "page": 1, "section": "middle"}}
      ]
    }},
    "auto_annual_premium": {{
      "status": "WARN",
      "values": [
        {{"document": "Quote.pdf", "page": 2, "section": "bottom - premium breakdown", "value": "$1,245.00"}},
        {{"document": "Application.pdf", "page": 3, "section": "middle - payment details", "value": "$1,250.00"}}
      ]
    }}
  }}
}}

## IMPORTANT:
- Return ONLY valid JSON
- ALL signatures are DIGITAL — accept typed names, e-signature stamps, and digital annotations as valid signatures
- Only flag a signature as MISSING if the field is completely empty (blank line, underscores, or no content)
- Focus on things that STOP the process (missing fields, wrong policy numbers)
- Don't flag trivial formatting differences
- Normalize addresses/names before comparing (St=Street, Jon=Jonathan acceptable)
"""
        return prompt
    
    def quick_signature_check(self, pdf_path: str) -> Dict:
        """
        Quick check for signatures in a single document
        
        Args:
            pdf_path: Path to PDF file
            
        Returns:
            Dict with signature presence information
        """
        if not self.model:
            return {"error": "Vertex AI not configured"}
        
        try:
            with open(pdf_path, 'rb') as f:
                pdf_data = f.read()
            
            prompt = """IMPORTANT: All signatures in this document are DIGITAL/ELECTRONIC signatures, not handwritten.
A filled signature field may show: a typed name in cursive/signature font, an e-signature stamp, 
a "Digitally signed by" annotation, DocuSign/ZohoSign stamps, or printed initials/name in the field.
Only treat a signature as BLANK if the field is completely empty (just underscores or no content).

Analyze this document and identify:
1. How many signature lines are present
2. How many are filled (have digital signatures)
3. How many are blank/empty
4. Page numbers where signatures are needed
5. Any date fields that are blank

Return as JSON:
{
  "total_signature_lines": number,
  "filled_signatures": number,
  "blank_signatures": number,
  "signature_locations": [{"page": number, "filled": boolean}],
  "blank_date_fields": number,
  "ready_to_send": boolean,
  "issues": ["list of issues"]
}
"""
            
            parts = [
                Part.from_text(prompt),
                Part.from_data(data=pdf_data, mime_type='application/pdf')
            ]
            
            response = self.model.generate_content(parts)
            result_text = response.text.strip()
            
            # Clean up markdown
            if result_text.startswith('```json'):
                result_text = result_text[7:]
            if result_text.startswith('```'):
                result_text = result_text[3:]
            if result_text.endswith('```'):
                result_text = result_text[:-3]
            
            return json.loads(result_text.strip())
            
        except Exception as e:
            return {"error": str(e)}


# Singleton instance
_service_instance = None

def get_document_verification_service():
    """Get or create the document verification service instance"""
    global _service_instance
    if _service_instance is None:
        _service_instance = DocumentVerificationService()
    return _service_instance
