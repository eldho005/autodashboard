"""
PDF Parser for MVR and DASH Reports
Extracts relevant driver information from uploaded PDF files
"""
import re
import json
from datetime import datetime
import PyPDF2
from io import BytesIO
import os
import base64

# Import prompts from schema file - using V2 simplified coverpage-focused schema
import quote_extraction_schema_v2
EXTRACTION_PROMPTS = quote_extraction_schema_v2.EXTRACTION_PROMPTS
QUOTE_TYPE_DETECTION_PROMPT = quote_extraction_schema_v2.QUOTE_TYPE_DETECTION_PROMPT
get_extraction_prompt = quote_extraction_schema_v2.get_extraction_prompt
transform_to_coverpage_format = quote_extraction_schema_v2.transform_to_coverpage_format
validate_extraction = quote_extraction_schema_v2.validate_extraction

# Vertex AI imports (optional - will use fallback if not available)
try:
    import vertexai
    from vertexai.generative_models import GenerativeModel, Part
    VERTEX_AI_AVAILABLE = True
except ImportError:
    VERTEX_AI_AVAILABLE = False
    print("[WARNING] Vertex AI not available, using fallback PDF parsing")


def to_sentence_case(text):
    """Convert text to sentence case (first letter uppercase, rest lowercase)"""
    if not text or not isinstance(text, str):
        return text
    return text[0].upper() + text[1:].lower() if len(text) > 0 else text


def parse_dash_pdf(pdf_file):
    """
    Parse DASH (Driver Abstract/Summary History) PDF and extract driver information
    
    Args:
        pdf_file: File object or bytes of the PDF
        
    Returns:
        dict: Extracted DASH information
    """
    try:
        print("\n[INFO] Starting DASH PDF parsing...")
        # Use pdfplumber for more robust text extraction
        import pdfplumber
        if isinstance(pdf_file, bytes):
            pdf_file = BytesIO(pdf_file)
        full_text = ""
        page_count = 0
        with pdfplumber.open(pdf_file) as pdf:
            print(f"[PDF] PDF has {len(pdf.pages)} pages")
            for page_idx, page in enumerate(pdf.pages):
                try:
                    page_text = page.extract_text()
                    if page_text:
                        full_text += page_text + "\n"
                        page_count += 1
                        print(f"  Page {page_idx + 1}: Extracted {len(page_text)} characters")
                    else:
                        print(f"  Page {page_idx + 1}: No text extracted (possibly scanned image)")
                except Exception as page_error:
                    print(f"  [ERROR] Error extracting text from page {page_idx + 1}: {str(page_error)}")
        
        print(f"[OK] Successfully extracted text from {page_count} page(s), total: {len(full_text)} characters")
        
        if not full_text or len(full_text.strip()) < 50:
            print("[WARNING] Extracted text is too short or empty")
            print(f"First 100 chars: {full_text[:100]}")

        # Parse the extracted text
        print("[PARSE] Calling extract_dash_fields...")
        dash_data = extract_dash_fields(full_text)
        
        print(f"[OK] DASH parsing complete. Extracted fields: {list(dash_data.keys())}")
        
        return {
            "success": True,
            "data": dash_data,
            "raw_text": full_text  # For debugging
        }
    except Exception as e:
        error_msg = f"PDF Parsing Error: {str(e)}"
        print(f"[ERROR] {error_msg}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": error_msg
        }

def extract_dash_fields(text):
    """
    Extract specific fields from DASH text
    """
    data = {}
    print("=== DASH PDF TEXT SAMPLE (First 2000 chars) ===")
    print(text[:2000])
    print("=== END SAMPLE ===")
    report_date_match = re.search(r'Report\s*Date:\s*(\d{4}-\d{1,2}\s*\d{1,2}-\d{1,2})', text, re.IGNORECASE)
    if report_date_match:
        # Remove any spaces from the date
        date_str = report_date_match.group(1).replace(' ', '')
        data['issue_date'] = normalize_date(date_str)
        data['report_date'] = normalize_date(date_str)
        print(f" Found Report Date: {data['report_date']}")
    
    # Driver Name - REMOVED: Name should only come from MVR
    # Name will be extracted from MVR PDF, not from DASH
    
    # Address - format: "Address: 201-1480 Eglinton Ave W ,Toronto,ON M6C2G5"
    address_patterns = [
        r'Address:\s*(.+?)\s+Number of',  # Get everything until "Number of"
        r'Address:\s*([^\n]+)',
    ]
    for pattern in address_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['address'] = match.group(1).strip()
            print(f"Found address: {data['address']}")
            break

    # Marital Status - format: "Marital Status: Married"
    marital_patterns = [
        r'Marital\s*Status\s*[:/\-]?\s*([A-Za-z ]+?)(?=\s+Number\b|\s+$)',
        r'Marital\s*Status\s*[:\s]+([A-Za-z ]+?)(?=\s+Number\b|\s+$)',
        r'Marital\s*[:\s]+([A-Za-z ]+?)(?=\s+Number\b|\s+$)'
    ]
    for pattern in marital_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['marital_status'] = match.group(1).strip()
            print(f"Found marital status: {data['marital_status']}")
            break
    # Fallback: parse from line containing 'Marital Status'
    if not data.get('marital_status'):
        for line in text.splitlines():
            if re.search(r'Marital\s*Status', line, re.IGNORECASE):
                parts = re.split(r'Marital\s*Status\s*[:/\-]?\s*', line, flags=re.IGNORECASE)
                if len(parts) > 1:
                    value = parts[1].strip()
                    # Trim trailing fields on the same line (e.g., "Number of ...")
                    value = re.split(r'\s+Number\b', value, flags=re.IGNORECASE)[0].strip()
                    if value:
                        data['marital_status'] = value
                        print(f"Found marital status (line fallback): {data['marital_status']}")
                        break
    
    # Gender - format: "Gender: Male Number of Comprehensive..."
    gender_patterns = [
        r'Gender\s*[:/\-]?\s*([A-Za-z]+?)(?=\s+Number\b|\s+$)',
        r'Gender\s*[:\s]+([A-Za-z]+?)(?=\s+Number\b|\s+$)',
        r'Sex\s*[:/\-]?\s*([A-Za-z]+?)(?=\s+Number\b|\s+$)'
    ]
    for pattern in gender_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['gender'] = match.group(1).strip()
            print(f"Found gender: {data['gender']}")
            break
    # Fallback: parse from line containing 'Gender'
    if not data.get('gender'):
        for line in text.splitlines():
            if re.search(r'Gender', line, re.IGNORECASE):
                parts = re.split(r'Gender\s*[:/\-]?\s*', line, flags=re.IGNORECASE)
                if len(parts) > 1:
                    value = parts[1].strip()
                    # Trim trailing fields on the same line (e.g., "Number of ...")
                    value = re.split(r'\s+Number\b', value, flags=re.IGNORECASE)[0].strip()
                    if value:
                        data['gender'] = value
                        print(f"Found gender (line fallback): {data['gender']}")
                        break
    
    # License Number - format: "DLN: G6043-37788-80203"
    license_patterns = [
        r'DLN:\s*([A-Z0-9\-]+)',  # DLN: G6043-37788-80203
        r'License\s*(?:Number|#|No\.?)?[:\s]+([A-Z0-9\-]+)',
        r'DL\s*(?:Number|#)?[:\s]+([A-Z0-9\-]+)',
    ]
    for pattern in license_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['license_number'] = match.group(1).strip()
            break
    
    # Date of Birth - REMOVED: DOB should only come from MVR
    # Date of Birth will be extracted from MVR PDF, not from DASH
    
    # Expiry Date
    expiry_patterns = [
        r'Expir(?:y|ation)\s*Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'Exp\.?\s*Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'Valid\s*(?:Through|Until)[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
    ]
    for pattern in expiry_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['expiry_date'] = normalize_date(match.group(1))
            break
    
    # Issue/Renewal Date
    issue_patterns = [
        r'Report\s*Date[:\s]+(\d{4}-\d{2}-\d{2})',  # Report Date: 2025-01-05
        r'Issue\s*Date[:\s]+(\d{4}-\d{2}-\d{2})',
        r'Issue\s*Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'Issued[:\s]+(\d{4}-\d{2}-\d{2})',
        r'Issued[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'Renewal\s*Date[:\s]+(\d{4}-\d{2}-\d{2})',
        r'Renewal\s*Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'(?:Issue|Issued)[:\s]+(\d{4}-\d{2}-\d{2})'  # DASH format: 2025-01-05
    ]
    for pattern in issue_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['issue_date'] = normalize_date(match.group(1))
            print(f" Found issue/renewal/report date: {data['issue_date']} (pattern: {pattern[:30]}...)")
            break
    
    if not data.get('issue_date'):
        print("[WARNING] No issue/renewal/report date found in PDF")
    
    # Class
    class_patterns = [
        r'Class[:\s]+([A-Z0-9]+)',
        r'License\s*Class[:\s]+([A-Z0-9]+)'
    ]
    for pattern in class_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['license_class'] = match.group(1).strip()
            break
    
    # VIN and Vehicle info: Extract ALL VEHICLES from Policy #1 section ONLY
    # Find Policy #1, then extract up to Policy #2 (or end if no Policy #2)
    
    policy1_pos = text.find('Policy #1')
    policy1_vehicles_list = []  # Array to store ALL vehicles from Policy #1
    
    print(f"\n[VEHICLES] Searching for 'Policy #1'...")
    print(f"[VEHICLES] policy1_pos = {policy1_pos}")
    print(f"[VEHICLES] Total text length: {len(text)} chars")
    
    # Also search for vehicle section
    vehicle_search = text.find('Vehicle #')
    print(f"[VEHICLES] Searching for 'Vehicle #'... found at position: {vehicle_search}")
    
    if vehicle_search >= 0:
        print(f"[VEHICLES] Vehicle section found! Text around it (500 chars):")
        start = max(0, vehicle_search - 100)
        end = min(len(text), vehicle_search + 400)
        print(f"{text[start:end]}")
    else:
        print(f"[VEHICLES] ❌ NO 'Vehicle #' found in entire PDF text!")
        print(f"[VEHICLES] Searching for alternative vehicle patterns...")
        
        # Check for other possible patterns
        if 'VIN' in text:
            print(f"[VEHICLES] Found 'VIN' in text")
            vin_pos = text.find('VIN')
            print(f"[VEHICLES] Text around VIN (300 chars):")
            start = max(0, vin_pos - 100)
            end = min(len(text), vin_pos + 200)
            print(f"{text[start:end]}")
        
        if 'VEHICLE' in text.upper():
            print(f"[VEHICLES] Found 'VEHICLE' (uppercase) in text")
            veh_pos = text.upper().find('VEHICLE')
            print(f"[VEHICLES] Text around VEHICLE (300 chars):")
            start = max(0, veh_pos - 100)
            end = min(len(text), veh_pos + 200)
            print(f"{text[start:end]}")
    
    if policy1_pos >= 0:
        # Find the NEXT policy number after Policy #1
        # Search from after "Policy #1" to find "Policy #2", "Policy #3", etc.
        remaining_text = text[policy1_pos + len('Policy #1'):]
        
        next_policy_match = re.search(r'Policy\s*#(\d+)', remaining_text)
        
        if next_policy_match:
            # Policy #1 section ends where the next policy begins
            next_policy_pos = policy1_pos + len('Policy #1') + next_policy_match.start()
            policy1_section = text[policy1_pos:next_policy_pos]
            print(f"[VEHICLES] Found next policy, Policy #1 section size: {len(policy1_section)} chars")
        else:
            # No next policy, take the rest of the document
            policy1_section = text[policy1_pos:]
            print(f"[VEHICLES] No next policy found, taking rest of doc. Policy #1 section size: {len(policy1_section)} chars")
        
        print(f"[VEHICLES] Policy #1 section (first 1000 chars):\n{policy1_section[:1000]}")
        print(f"[VEHICLES] ...")
        print(f"[VEHICLES] Policy #1 section (last 500 chars):\n{policy1_section[-500:]}")
        
        # Extract ALL vehicles from Policy #1 by finding all "Vehicle #N:" patterns
        # Split by any Vehicle #N pattern to get all vehicle blocks
        vehicle_blocks = re.split(r'Vehicle\s*#(\d+):\s*', policy1_section, flags=re.IGNORECASE)
        
        print(f"[VEHICLES] Split result: {len(vehicle_blocks)} blocks")
        if len(vehicle_blocks) > 1:
            print(f"[VEHICLES] vehicle_blocks structure: {[type(b).__name__ + f'(len={len(b)})' for b in vehicle_blocks[:5]]}")
        else:
            print(f"[VEHICLES] ❌ No Vehicle #N pattern matched! Regex didn't split anything")
        
        # vehicle_blocks will be: ['text_before', 'num1', 'content1', 'num2', 'content2', ...]
        # Process pairs: (vehicle_number, vehicle_content)
        
        for i in range(1, len(vehicle_blocks), 2):
            if i + 1 < len(vehicle_blocks):
                vehicle_num = vehicle_blocks[i].strip()
                block = vehicle_blocks[i + 1]
                
                print(f"[VEHICLES] Processing Vehicle #{vehicle_num}...")
                print(f"[VEHICLES]   Block content (first 200 chars): {block[:200]}")
                
                # Check if this block contains a VIN (17-char code)
                vin_match = re.search(r'([A-HJ-NPR-Z0-9]{17})', block)
                
                if vin_match and not re.match(r'^(Principal Operator|Named Insured|Self|Spouse)', block.strip(), re.IGNORECASE):
                    # This block has a VIN and is not just a role label
                    vin = vin_match.group(1).strip().upper()
                    
                    # Extract year/make/model - everything up to the VIN
                    vehicle_line = block[:vin_match.start()].strip()
                    
                    # Take only the first line
                    lines = vehicle_line.split('\n')
                    vehicle_info = lines[0].strip() if lines else vehicle_line
                    
                    # Clean up the text
                    vehicle_info = re.sub(r'\s+', ' ', vehicle_info)  # collapse whitespace
                    vehicle_info = vehicle_info.rstrip(' -/').strip()   # remove trailing separators
                    
                    # Skip if it's empty or just a role
                    if vehicle_info and not re.match(r'^(Principal Operator|Named Insured|Self|Spouse|DLN|Ontario|Relationship)', vehicle_info, re.IGNORECASE):
                        policy1_vehicles_list.append({
                            'vehicle_number': vehicle_num,
                            'vin': vin,
                            'year_make_model': vehicle_info
                        })
                        print(f"[VEHICLES] ✅ Found Vehicle #{vehicle_num}: {vehicle_info} | VIN: {vin}")
                else:
                    print(f"[VEHICLES] ❌ No valid VIN found in block or block is role label")
        
        if not policy1_vehicles_list:
            print("[VEHICLES] ❌ No vehicles with VIN found in Policy #1 section")
    else:
        print("[VEHICLES] ❌ Policy #1 not found in PDF")
    
    print(f"[VEHICLES] FINAL RESULT: {len(policy1_vehicles_list)} vehicles extracted")
    print(f"[VEHICLES] policy1_vehicles_list = {policy1_vehicles_list}")
    
    # Store all vehicles from Policy #1 for frontend to render
    data['policy1_vehicles'] = policy1_vehicles_list
    
    # For backward compatibility, set single vehicle fields (use first vehicle if available)
    if policy1_vehicles_list:
        data['vin'] = policy1_vehicles_list[0]['vin']
        data['vehicle_year_make_model'] = policy1_vehicles_list[0]['year_make_model']
    else:
        data['vin'] = '-'
        data['vehicle_year_make_model'] = '-'
    
    data['extracted_from_policy'] = '1'  # Indicates this is from Policy #1
    
    # Years of Continuous Insurance
    cont_ins_match = re.search(r'Years\s+of\s+Continuous\s+Insurance:\s*(\d+)', text, re.IGNORECASE)
    if cont_ins_match:
        data['years_continuous_insurance'] = cont_ins_match.group(1)
        print(f" Years of Continuous Insurance: {data['years_continuous_insurance']}")
    
    # Policy dates for gap calculation
    # DASH Report format: Extract from Policies table
    # Pattern: #N YYYY-MM-DD to YYYY-MM-DD [Company] [Extra] [STATUS/REASON]
    # Status appears in rightmost column of the table
    policies_section_match = re.search(r'Policies\s*\n(.+?)(?:Claims|Previous Inquiries|Page \d+|$)', text, re.DOTALL | re.IGNORECASE)
    
    if policies_section_match:
        policies_text = policies_section_match.group(1)
        # Split by lines and process each policy row
        all_policies = []
        
        for line in policies_text.split('\n'):
            line = line.strip()
            if not line or 'Note:' in line or 'Information' in line:
                continue
            
            # Look for pattern: #N (number) followed by dates
            match = re.search(r'#(\d+)\s+(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})', line)
            
            if match:
                policy_num = int(match.group(1))
                start_date = normalize_date(match.group(2))
                end_date = normalize_date(match.group(3))
                
                # Extract company name - look for text after dates
                # It usually comes before any status indicator
                company = ''
                remaining_text = line[match.end():].strip()
                
                # Split by overlap, cancelled, expired, etc. to separate company from status
                import re as regex_module
                status_pattern = r'(\*OVERLAP\*|Cancelled|Expired|Suspended|Non-Renewal|Active|active)'
                status_match = regex_module.search(status_pattern, remaining_text, regex_module.IGNORECASE)
                
                if status_match:
                    company = remaining_text[:status_match.start()].strip()
                else:
                    # If no status keyword found, take first part as company
                    parts = remaining_text.split()
                    if parts and len(parts[0]) > 5:  # company names are usually longer
                        company = remaining_text.split('*')[0].strip()
                
                # Extract status/reason
                status = 'active'  # default
                
                if remaining_text:
                    if 'cancelled' in remaining_text.lower():
                        status = 'cancelled'
                        if '-' in remaining_text.lower():
                            status = 'cancelled - ' + remaining_text.split('-', 1)[1].strip().lower()
                    elif 'expired' in remaining_text.lower():
                        status = 'expired'
                    elif 'suspended' in remaining_text.lower():
                        status = 'suspended'
                    elif 'non-renewal' in remaining_text.lower():
                        status = 'non-renewal'
                    else:
                        status = 'active'
                
                policy = {
                    'number': policy_num,
                    'start_date': start_date,
                    'end_date': end_date,
                    'company': company,
                    'status': status
                }
                all_policies.append(policy)
                print(f"  Policy #{policy_num}: {start_date} to {end_date} - Company: {company} - Status: {status}")
        
        # If we found policies, store them
        if all_policies:
            data['all_policies'] = all_policies
            print(f" Extracted {len(all_policies)} policies with status from DASH Report table")
            
            # Get the FIRST policy from the PDF
            first_policy_data = all_policies[0]
            
            # First insurance = start date of first policy in the list
            data['first_insurance_date'] = first_policy_data['start_date']
            
            # Renewal date = first policy's expiry date
            data['renewal_date'] = first_policy_data['end_date']
            
            # Policy end date = first policy's expiry date
            data['policy_end_date'] = first_policy_data['end_date']
            
            # Get the LAST policy (current/latest one) for policy_start_date
            last_policy_data = all_policies[-1]
            data['policy_start_date'] = last_policy_data['start_date']
            
            print(f" First Insurance Date (from first policy in list): {data['first_insurance_date']}")
            print(f" Renewal Date (First policy Expiry): {data['renewal_date']}")
            print(f" Current Policy Start Date (from last policy): {data['policy_start_date']}")

    
    # Fallback: Try to get from detail section if policies section not found
    if not data.get('policy_start_date'):
        earliest_term_match = re.search(r'Start\s+of\s+the\s+Earliest\s+Term:\s*(\d{4}-\d{2}-\d{2})', text)
        if earliest_term_match:
            data['policy_start_date'] = normalize_date(earliest_term_match.group(1))
            print(f" Policy Start Date (fallback): {data['policy_start_date']}")
    
    if not data.get('policy_end_date'):
        latest_term_match = re.search(r'End\s+of\s+the\s+Latest\s+Term:\s*(\d{4}-\d{2}-\d{2})', text)
        if latest_term_match:
            data['policy_end_date'] = normalize_date(latest_term_match.group(1))
            print(f" Policy End Date (fallback): {data['policy_end_date']}")
    
    # Status
    status_patterns = [
        r'Status[:\s]+(Valid|Active|Suspended|Revoked|Expired)',
        r'License\s*Status[:\s]+(Valid|Active|Suspended|Revoked|Expired)'
    ]
    for pattern in status_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['license_status'] = match.group(1).strip()
            break
    
    # Demerit Points
    points_patterns = [
        r'(?:Demerit\s*)?Points?[:\s]+(\d+)',
        r'Point\s*Balance[:\s]+(\d+)',
        r'Current\s*Points[:\s]+(\d+)'
    ]
    for pattern in points_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['demerit_points'] = match.group(1)
            break
    
    # Conditions/Restrictions
    conditions_match = re.search(r'Conditions?[:\s]+([^\n]+)', text, re.IGNORECASE)
    if conditions_match:
        data['conditions'] = conditions_match.group(1).strip()
    
    # Claims History - extract ONLY from the "Claims" section
    # NOT from the "Policies" section
    claims = []
    
    print("\n=== EXTRACTING CLAIMS ===")
    
    # Find the "Claims" section in the PDF - improved to capture all claims across pages
    # Look for Claims section and capture until "Previous Inquiries" section
    claims_section_match = re.search(r'Claims\s*\n(.*?)(?:Previous Inquiries)', text, re.DOTALL | re.IGNORECASE)
    
    # If not found, try alternative: capture from Claims to end of document
    if not claims_section_match:
        claims_section_match = re.search(r'Claims\s*\n(.*?)$', text, re.DOTALL | re.IGNORECASE)
    
    if claims_section_match:
        claims_text = claims_section_match.group(1)
        print(f"\n=== CLAIMS SECTION ({len(claims_text)} chars) ===")
        print(f"First 1000 chars:\n{claims_text[:1000]}")
        
        # Count potential claim markers
        claim_num_markers = re.findall(r'#(\d+)', claims_text)
        print(f"\n[DEBUG] Found claim number markers: {claim_num_markers}")
        print(f"[DEBUG] Total markers found: {len(claim_num_markers)}")
        
        # Strategy: Split by claim numbers and extract data from each section
        print(f"\n[EXTRACT] Splitting by claim number patterns...")
        
        # Split by # followed by digit
        parts = re.split(r'(?=#\d)', claims_text)
        claim_matches = []
        
        for part_idx, part in enumerate(parts):
            part = part.strip()
            if not part or not part.startswith('#'):
                continue
                
            # Extract claim number
            num_match = re.match(r'#(\d+)', part)
            if not num_match:
                continue
                
            claim_num = num_match.group(1)
            print(f"\n[CLAIM {claim_num}] Processing...")
            print(f"[CLAIM {claim_num}] TEXT PREVIEW: {part[:800]}")  # DEBUG: Show first 800 chars
            
            # Extract date of loss
            date_match = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', part)
            loss_date = date_match.group(1) if date_match else "0000-00-00"
            print(f"  Date: {loss_date}")
            
            # Extract at-fault percentage
            fault_match = re.search(r'At-?Fault\s*:\s*(\d+)\s*%', part, re.IGNORECASE)
            at_fault_pct = fault_match.group(1) if fault_match else "0"
            print(f"  At-Fault: {at_fault_pct}%")
            
            # Extract company name (usually between Date and At-Fault)
            company_match = re.search(rf'#{claim_num}\s+.*?(\d{{4}}[-/]\d{{1,2}}[-/]\d{{1,2}})\s+(.*?)(?:At-?Fault|$)', part, re.IGNORECASE | re.DOTALL)
            company = company_match.group(2).strip() if company_match else ""
            if company:
                company = company.split('\n')[0].strip()  # Take first line only
            print(f"  Company: {company}")
            
            # Create a match-like object to maintain compatibility
            class PartMatch:
                def __init__(self, num, date, company, fault, full_text):
                    self._num = num
                    self._date = date
                    self._company = company
                    self._fault = fault
                    self._text = full_text
                def group(self, idx):
                    if idx == 0: return self._text
                    elif idx == 1: return self._num
                    elif idx == 2: return self._date
                    elif idx == 3: return self._company
                    elif idx == 4: return self._fault
                    return None
                def groups(self):
                    return (self._num, self._date, self._company, self._fault)
                def end(self):
                    return len(self._text)
            
            claim_matches.append(PartMatch(claim_num, loss_date, company, at_fault_pct, part))
        
        print(f"\n[EXTRACT] Total claims extracted: {len(claim_matches)}")
        print(f"[EXTRACT] Expected: {len(claim_num_markers)}, Found: {len(claim_matches)}")
        
        total_claims_found = len(claim_matches)
        print(f"\n[CLAIMS] TOTAL FOUND: {total_claims_found}")
        
        for idx, match in enumerate(claim_matches, 1):
            claim = {}
            print(f"\n[CLAIM {idx}/{total_claims_found}] Processing...")
            claim_num = match.group(1)
            loss_date = match.group(2)
            
            # Extract company name and at-fault percentage
            if len(match.groups()) >= 4:
                # We have the full match with company and at-fault
                company_and_notes = match.group(3).strip()
                at_fault_pct = match.group(4)
                print(f"  [FULL] Company='{company_and_notes}', AtFault={at_fault_pct}%")
            else:
                # Fallback: extract from text after the loss date
                company_and_notes = ""
                at_fault_pct = "0"
                # Try to find at-fault percentage in the text following this claim
                search_start = match.end()
                search_end = min(search_start + 200, len(claims_text))
                next_section = claims_text[search_start:search_end]
                at_fault_match = re.search(r'At-?Fault\s*:\s*(\d+)\s*%', next_section, re.IGNORECASE)
                if at_fault_match:
                    at_fault_pct = at_fault_match.group(1)
                    # Extract company from between loss date and at-fault
                    company_section = next_section[:at_fault_match.start()]
                    company_and_notes = company_section.strip()
                    print(f"  [PARTIAL] Company='{company_and_notes}', AtFault={at_fault_pct}%")
            
            claim['date'] = normalize_date(loss_date)
            
            # Extract FIRST PARTY DRIVER NAME
            # In DASH reports, look for "First Party Driver:" label followed by the name
            # Search in the current claim section first, then in the full PDF text
            first_party_driver = ''
            first_party_match = re.search(r'First\s+Party\s+Driver\s*:\s*([A-Z][A-Za-z\s\-\']+(?:,\s*[A-Z][A-Za-z\s\-\']+)?)', part, re.IGNORECASE)
            if first_party_match:
                first_party_driver = first_party_match.group(1).strip()
            else:
                # If not found in claim section, search the full PDF text around this claim date
                # Create a pattern to find the claim by date and extract details after it
                claim_detail_pattern = rf'Claim\s*#[:\s]*{claim_num}\s+Date\s+of\s+Loss\s+{re.escape(loss_date)}.*?First\s+Party\s+Driver\s*:\s*([A-Z][A-Za-z\s\-\']+(?:,\s*[A-Z][A-Za-z\s\-\']+)?)'
                full_match = re.search(claim_detail_pattern, text, re.DOTALL | re.IGNORECASE)
                if full_match:
                    first_party_driver = full_match.group(1).strip()
                    # Clean up - remove anything after newline or extra content
                    first_party_driver = first_party_driver.split('\n')[0].strip()
                    # Also remove trailing text like "DLN" if it got included
                    first_party_driver = re.sub(r'\s+(DLN|Date\s+of|Listed|Excl|Convict).*$', '', first_party_driver, flags=re.IGNORECASE)
            
            # Clean up the name - remove newlines and extra whitespace
            first_party_driver = first_party_driver.split('\n')[0].strip() if first_party_driver else ''
            first_party_driver = re.sub(r'\s+(DLN|Date\s+of|Listed|Excl|Convict).*$', '', first_party_driver, flags=re.IGNORECASE)
            
            if first_party_driver:
                claim['firstPartyDriver'] = first_party_driver
                print(f"  [FOUND] First Party Driver: {first_party_driver}")
            else:
                print(f"  [NOT FOUND] First Party Driver")
            
            # Extract company name and check for THIRD PARTY indicator
            company = re.sub(r'\*.*?\*', '', company_and_notes).strip()
            claim['company'] = company
            
            # Extract THIRD PARTY DRIVER NAME
            # If company contains "*THIRD PARTY*" or similar, extract the third party name
            third_party_match = re.search(r'\*?THIRD\s*PARTY\*?\s*[-:\s]*([A-Z][A-Z\s\-\']+,\s*[A-Z][A-Za-z\s\-\']+)?', company_and_notes, re.IGNORECASE)
            if third_party_match and third_party_match.group(1):
                claim['thirdPartyDriver'] = third_party_match.group(1).strip()
                print(f"  Third Party Driver: {claim['thirdPartyDriver']}")
            elif re.search(r'\*?THIRD\s*PARTY\*?', company_and_notes, re.IGNORECASE):
                # Third party claim but no explicit name extracted, use company as fallback
                claim['thirdPartyDriver'] = company.replace('*THIRD PARTY*', '').strip() or 'Third Party'
                print(f"  Third Party Driver (from company): {claim['thirdPartyDriver']}")
            
            
            # At-fault
            if at_fault_pct == '0':
                claim['fault'] = 'No'
            elif at_fault_pct == '100':
                claim['fault'] = 'Yes'
            else:
                claim['fault'] = f'{at_fault_pct}%'
            
            # Extract coverage information - search in the full PDF for detailed claim sections
            coverage = ''
            print(f"\n  [INFO] Claim #{claim_num} - Extracting Coverage...")
            print(f"  [INFO] Claim Company: '{company}'")
            print(f"  [INFO] Claim Date: '{loss_date}'")
            
            # Coverage is in the detailed claim pages (usually pages 12+)
            # Pattern: Claim #X Date of Loss YYYY-MM-DD ... Coverage: VALUE
            print(f"  -> SEARCHING for Coverage in full PDF text for Claim #{claim_num}...")
            
            # Build a detailed search pattern for this specific claim
            detailed_pattern = rf'Claim\s*#\s*{claim_num}\b.*?Date\s+of\s+Loss\s+{re.escape(loss_date)}.*?Coverage\s*:\s*([A-Za-z0-9\-\/\,\s]+?)(?:\n|Policyholder|Vehicle|At-Fault|$)'
            detailed_match = re.search(detailed_pattern, text, re.DOTALL | re.IGNORECASE)
            
            if detailed_match:
                coverage = detailed_match.group(1).strip()
                coverage = coverage.split('\n')[0].strip()
                claim['coverage'] = coverage
                print(f"  ✅ COVERAGE FOUND (detailed pages): '{coverage}'")
            else:
                # Try simpler pattern
                print(f"  ❌ Detailed pattern not found, trying simpler search...")
                claim_pattern = rf'Claim\s*#\s*{claim_num}\b.*?Coverage\s*:\s*([A-Za-z0-9\-\/\,\s]+?)(?:\n|Policyholder|Vehicle|$)'
                simple_match = re.search(claim_pattern, text, re.DOTALL | re.IGNORECASE)
                if simple_match:
                    coverage = simple_match.group(1).strip()
                    coverage = coverage.split('\n')[0].strip()
                    claim['coverage'] = coverage
                    print(f"  ✅ COVERAGE FOUND (simple pattern): '{coverage}'")
            
            # Ensure coverage field always exists
            if 'coverage' not in claim:
                claim['coverage'] = ''
            
            print(f"  -> Final coverage value: '{claim.get('coverage', '')}'")
            
            # Try to find claim details in the detailed section below
            # Look for the specific claim number section and extract financial details
            claim_detail_pattern = rf'Claim #{claim_num}\s+Date of Loss\s+\d{{4}}-\d{{2}}-\d{{2}}.*?Total Loss:\s*\$\s*([\d,\.]+).*?Total Expense:\s*\$\s*([\d,\.]+)'
            detail_match = re.search(claim_detail_pattern, text, re.DOTALL | re.IGNORECASE)
            
            if detail_match:
                loss_val = detail_match.group(1).replace(',', '').strip()
                expense_val = detail_match.group(2).replace(',', '').strip()
                
                claim['loss'] = loss_val
                claim['expense'] = expense_val
                
                # Calculate total
                try:
                    total = float(loss_val) + float(expense_val)
                    claim['total'] = f'{total:.2f}'
                    print(f"  -> Financials: Loss=${loss_val}, Expense=${expense_val}, Total=${total:.2f}")
                except ValueError:
                    print(f"  [WARNING] Could not calculate total for claim #{claim_num}")
                
                # Extract KOL (claim loss details) items like "KOL16 - Other Property Damage..."
                # Pattern: KOL## - Description: $X (Loss); $Y (Expense);
                # Search in the current claim section and the full PDF
                kol_matches = []
                
                # Improved regex to handle variations in spacing and newlines
                # Match KOL## followed by description, then amounts
                kol_pattern = r'(KOL\d+\s*[-–]\s*[^\n:]+?):\s*\$\s*([\d,\.]+)\s*\(Loss\);\s*\$\s*([\d,\.]+)\s*\(Expense\);'
                kol_matches = re.findall(kol_pattern, part, re.IGNORECASE)
                
                # If not found in claim section, search full PDF for this specific claim's details
                if not kol_matches:
                    # Search a large area around the claim number to find KOL items
                    # Look for "Claim #X" and capture everything until the next "Claim #" or end of document
                    claim_section_pattern = rf'Claim\s*#\s*{claim_num}.*?(?=Claim\s*#\d+|Convictions|$)'
                    claim_section_match = re.search(claim_section_pattern, text, re.DOTALL | re.IGNORECASE)
                    if claim_section_match:
                        claim_section_text = claim_section_match.group(0)
                        kol_matches = re.findall(kol_pattern, claim_section_text, re.IGNORECASE)
                
                if kol_matches:
                    kol_items = []
                    for kol_desc, kol_loss, kol_expense in kol_matches:
                        # Clean up description - remove extra whitespace and newlines
                        clean_desc = ' '.join(kol_desc.split())
                        kol_items.append({
                            'description': clean_desc.strip(),
                            'loss': kol_loss.strip(),
                            'expense': kol_expense.strip()
                        })
                    claim['kolItems'] = kol_items
                    print(f"  -> Found {len(kol_items)} loss detail items (KOL)")
                    for item in kol_items:
                        print(f"     • {item['description']}: ${item['loss']} (Loss), ${item['expense']} (Expense)")
                else:
                    print(f"  -> No loss detail items (KOL) found for this claim")
            else:
                print(f"  [WARNING] No financial details found for claim #{claim_num}")
            
            # Try to find claim status
            status_pattern = rf'Claim #{claim_num}.*?Claim\s*Status:\s*(\w+)'
            status_match = re.search(status_pattern, text, re.DOTALL | re.IGNORECASE)
            if status_match:
                claim['status'] = status_match.group(1).strip()
            else:
                claim['status'] = 'Closed'  # Default if not found
            
            print(f" Claim #{claim_num}: {claim['date']}, Company={claim['company']}, At-Fault={claim['fault']}, Status={claim.get('status', 'N/A')}, Coverage={claim.get('coverage', 'MISSING')}")
            print(f"  [DEBUG] Full claim keys before append: {list(claim.keys())}")
            claims.append(claim)
    else:
        print("[WARNING] No 'Claims' section found in PDF")
    
    print(f"\n FINAL: {len(claims)} claims extracted from Claims section")
    
    if claims:
        data['claims'] = claims
        data['claims_count'] = str(len(claims))
        print(f" Returning {len(claims)} valid claims\n")
    else:
        data['claims'] = []
        data['claims_count'] = '0'
        print(f"[INFO] No valid claims found in PDF\n")
    
    # Email
    email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    if email_match:
        data['email'] = email_match.group(0)
    
    # Phone number - if present
    phone_patterns = [
        r'Phone[:\s]+(\+?[\d\-\(\)\s]+)',
        r'Tel[:\s]+(\+?[\d\-\(\)\s]+)',
        r'Mobile[:\s]+(\+?[\d\-\(\)\s]+)'
    ]
    for pattern in phone_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['phone'] = match.group(1).strip()
            print(f"Found phone: {data['phone']}")
            break
    
    # Email - if present
    email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    if email_match:
        data['email'] = email_match.group(0)
        print(f"Found email: {data['email']}")
    
    # Extract Policy #1 Expiry Date from the policy detail section (NOT the "to" date from policies list)
    # The "to" date might be the cancellation date, but "Expiry Date:" is the actual expiry
    if policy1_pos >= 0:
        # Get Policy #1 section
        remaining_text = text[policy1_pos + len('Policy #1'):]
        next_policy_match = re.search(r'Policy\s*#(\d+)', remaining_text)
        
        if next_policy_match:
            next_policy_pos = policy1_pos + len('Policy #1') + next_policy_match.start()
            policy1_section = text[policy1_pos:next_policy_pos]
        else:
            policy1_section = text[policy1_pos:]
        
        # Look for "Expiry Date: YYYY-MM-DD" or "Expiry Date: MM/DD/YYYY"
        expiry_date_patterns = [
            r'Expiry\s*Date:\s*(\d{4}-\d{1,2}-\d{1,2})',  # YYYY-MM-DD format
            r'Expiry\s*Date:\s*(\d{1,2}/\d{1,2}/\d{4})',  # MM/DD/YYYY format
        ]
        
        policy1_expiry_found = False
        for pattern in expiry_date_patterns:
            expiry_match = re.search(pattern, policy1_section, re.IGNORECASE)
            if expiry_match:
                policy1_expiry_date = normalize_date(expiry_match.group(1))
                data['renewal_date'] = policy1_expiry_date
                policy1_expiry_found = True
                print(f" Found Policy #1 Expiry Date: {policy1_expiry_date}")
                print(f" Updated Renewal Date to Policy #1 Expiry Date: {data['renewal_date']}")
                break
        
        if not policy1_expiry_found:
            print(f" Policy #1 Expiry Date not found in policy detail section, using policy list end_date")
    
    # Log what was extracted
    extracted_fields = [k for k, v in data.items() if v]
    print(f"\n[OK] DASH extraction complete:")
    print(f"  Fields extracted: {extracted_fields}")
    print(f"  Total fields with values: {len(extracted_fields)} out of {len(data)}")
    print(f"=== DASH EXTRACTED DATA ===")
    print(json.dumps(data, indent=2, default=str))
    print(f"=== END DATA ===")
    
    if not extracted_fields:
        print("\n[WARNING] WARNING: No fields were extracted from the PDF text!")
        print("This could mean:")
        print("  1. PDF is image-based/scanned (needs OCR)")
        print("  2. Text layout doesn't match expected patterns")
        print("  3. PDF is corrupted")
    
    return data


def parse_quote_pdf(pdf_file):
    """
    Parse Auto Quote PDF and extract policy, vehicle, and coverage information

    Args:
        pdf_file: File object or bytes of the PDF

    Returns:
        dict: Extracted quote information
    """
    try:
        if isinstance(pdf_file, bytes):
            pdf_file = BytesIO(pdf_file)

        pdf_reader = PyPDF2.PdfReader(pdf_file)

        full_text = ""
        for page in pdf_reader.pages:
            full_text += (page.extract_text() or "") + "\n"

        quote_data = extract_quote_fields(full_text)

        return {
            "success": True,
            "data": quote_data,
            "raw_text": full_text
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def extract_quote_fields(text):
    """
    Extract key fields from Auto Quote PDF text
    """
    data = {}
    normalized = re.sub(r"\s+", " ", text).strip()

    # Effective Date (handles spacing within letters from PDF extraction)
    effective_match = re.search(
        r"E\s*f\s*f\s*e\s*c\s*t\s*i\s*v\s*e\s*D\s*a\s*t\s*e\s*:\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{2,4})",
        text,
        re.IGNORECASE
    )
    if effective_match:
        data["effective_date"] = normalize_date(effective_match.group(1))

    # Insurance Company (fix broken 'Company' word)
    company_match = re.search(
        r"([A-Z][A-Za-z&\s]+\s+Insurance\s+C\s*o\s*m\s*p\s*a\s*n\s*y|[A-Z][A-Za-z&\s]+\s+Insurance\s+Inc\.?|[A-Z][A-Za-z&\s]+\s+Insurance)\b",
        text,
        re.IGNORECASE
    )
    if company_match:
        company = company_match.group(1)
        company = re.sub(r"\s+", " ", company).strip()
        company = re.sub(r"Comp\s*any", "Company", company, flags=re.IGNORECASE)
        data["insurance_company"] = company

    # Policy Holder (after 'Breakdown')
    holder_match = re.search(r"Breakdown\s*([A-Z][A-Z\s]+)", text)
    if holder_match:
        policy_holder = re.sub(r"\s+", " ", holder_match.group(1)).strip()
        data["policy_holder"] = policy_holder

    # Vehicle year/make/model
    vehicle_match = re.search(
        r"Private\s*P\s*assenger\s*-\s*(\d{4}\s+[A-Z0-9\s]+?)\s+\$",
        text,
        re.IGNORECASE
    )
    if vehicle_match:
        data["vehicle_year_make_model"] = re.sub(r"\s+", " ", vehicle_match.group(1)).strip()

    # Coverages and limits
    bi_match = re.search(r"Bodily\s*Injury\s*\$?([0-9,]+)", normalized, re.IGNORECASE)
    pd_match = re.search(r"Property\s*Damage\s*\$?([0-9,]+)", normalized, re.IGNORECASE)
    dc_match = re.search(r"Direct\s*Compensation\s*\$?([0-9,]+)\s*Ded\.?\s*\$?([0-9,]+)", normalized, re.IGNORECASE)
    
    # All Perils - Capture the FIRST amount (deductible) not the second amount (premium)
    # Pattern: "All Perils $1,000 Ded. $493" - capture $1,000 (the deductible amount before "Ded.")
    all_perils_match = re.search(r"All\s+Perils[\s\xa0]*\$?([0-9,]+)[\s\xa0]*Ded", normalized, re.IGNORECASE)
    # Fallback: "All Perils Ded. $1,000" or "All Perils Deductible: $1,000"
    if not all_perils_match:
        all_perils_match = re.search(r"All\s+Perils[\s\xa0]*(?:Ded\.?|Deductible:?)[\s\xa0]*\$?([0-9,]+)", normalized, re.IGNORECASE)
    
    loss_use_match = re.search(r"#?20\s*L\s*o\s*s\s*s\s*of\s*U\s*s\s*e\s*\$?([0-9,]+)", normalized, re.IGNORECASE)
    fam_prot_match = re.search(r"#\s*44[^$]*\$?([0-9,]+)", normalized, re.IGNORECASE)
    if not fam_prot_match:
        fam_prot_match = re.search(
            r"F\s*a\s*m\s*i\s*l\s*y\s*P\s*r\s*o\s*t\s*e\s*c\s*t\s*i\s*o\s*n\s*\$?([0-9,]+)",
            normalized,
            re.IGNORECASE
        )
    non_owned_match = re.search(r"#\s*27[^$]*\$?([0-9,]+)", normalized, re.IGNORECASE)
    if not non_owned_match:
        non_owned_match = re.search(
            r"N\s*o\s*n\s*-?\s*O\s*w\s*n\s*e\s*d\s*A\s*u\s*t\s*o\s*\$?([0-9,]+)",
            normalized,
            re.IGNORECASE
        )

    if bi_match:
        data["bodily_injury_limit"] = f"${bi_match.group(1)}"
    if pd_match:
        data["property_damage_limit"] = f"${pd_match.group(1)}"
    if data.get("bodily_injury_limit") or data.get("property_damage_limit"):
        bi_val = data.get("bodily_injury_limit", "-")
        pd_val = data.get("property_damage_limit", "-")
        data["bodily_injury_property_damage"] = f"{bi_val} / {pd_val}"

    if dc_match:
        data["direct_comp_limit"] = f"${dc_match.group(1)}"
        data["direct_comp_deductible"] = f"${dc_match.group(2)}"

    if all_perils_match:
        data["all_perils_deductible"] = f"${all_perils_match.group(1)}"

    if loss_use_match:
        data["loss_of_use_limit"] = f"${loss_use_match.group(1)}"

    if fam_prot_match:
        data["family_protection_limit"] = f"${fam_prot_match.group(1)}"

    if non_owned_match:
        data["non_owned_auto_limit"] = f"${non_owned_match.group(1)}"

    return data


def parse_mvr_pdf(pdf_file):
    """
    Parse MVR PDF and extract driver information
    
    Args:
        pdf_file: File object or bytes of the PDF
        
    Returns:
        dict: Extracted MVR information
    """
    try:
        # Read PDF
        if isinstance(pdf_file, bytes):
            pdf_file = BytesIO(pdf_file)
        
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        
        # Extract text from all pages
        full_text = ""
        for page in pdf_reader.pages:
            full_text += page.extract_text() + "\n"
        
        # Parse the extracted text
        mvr_data = extract_mvr_fields(full_text)
        
        # CRITICAL: Verify policy1_vehicles is in the response
        print(f"\n[PARSE_MVR] Verifying response data:")
        print(f"[PARSE_MVR] - 'policy1_vehicles' in mvr_data: {'policy1_vehicles' in mvr_data}")
        if 'policy1_vehicles' in mvr_data:
            print(f"[PARSE_MVR] - mvr_data['policy1_vehicles']: {mvr_data['policy1_vehicles']}")
        
        return {
            "success": True,
            "data": mvr_data,
            "raw_text": full_text  # For debugging
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


def extract_mvr_fields(text):
    """
    Extract specific fields from MVR text using regex patterns
    """
    data = {}
    
    print("=== MVR PDF TEXT SAMPLE (First 2000 chars) ===")
    print(text[:2000])
    print("=== END SAMPLE ===")
    
    # Extract text before DOB to search for name (names usually come before DOB)
    dob_pos = text.find('Birth Date')
    if dob_pos < 0:
        dob_pos = text.find('DOB')
    if dob_pos < 0:
        dob_pos = text.find('Date of Birth')
    
    search_text = text[:dob_pos] if dob_pos > 0 else text[:2000]
    print(f"=== NAME SEARCH AREA (before DOB, {len(search_text)} chars) ===")
    print(search_text)
    print("=== END NAME SEARCH AREA ===")
    
    # FIRST: Extract Full Name from MVR - Preserve exact format from MVR response
    # Method 1: Direct match for "Name: " followed by text until "Birth Date" or newline
    name_match = re.search(r'Name\s*:\s*([^\n]+?)(?=\s+(?:Birth|Gender|Address|Height|Demerit)|\n)', text, re.IGNORECASE)
    if name_match:
        name_raw = name_match.group(1).strip()
        # Convert each name part to sentence case
        name_parts = [to_sentence_case(part.strip()) for part in name_raw.split(',')]
        data['name'] = ','.join(name_parts)
        print(f" ✓ Found Name (converted to sentence case): {data['name']}")
    
    if 'name' not in data:
        print(f" ⚠️  WARNING: Could not extract name from MVR")
    else:
        print(f" ✓ FINAL NAME EXTRACTED: {data['name']}")
    
    # License Number - various patterns
    
    # License Number - various patterns
    license_patterns = [
        r'Licence Number:\s*([A-Z0-9\-]+)',  # MVR format
        r'License\s*(?:Number|#|No\.?)?[:\s]+([A-Z0-9\-]+)',
        r'DL\s*(?:Number|#|No\.?)?[:\s]+([A-Z0-9\-]+)',
        r'Driver[\'s]?\s*License[:\s]+([A-Z0-9\-]+)'
    ]
    for pattern in license_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['license_number'] = match.group(1).strip()
            print(f" Found License Number: {data['license_number']}")
            break
    
    # Expiry Date - MVR format: "Expiry Date: 03/02/2030"
    # NOTE: This is the DRIVER'S LICENSE expiry date, NOT the policy renewal date
    # Renewal date should only come from DASH PDF (Policy #1 Expiry Date)
    expiry_patterns = [
        r'Expiry Date:\s*(\d{1,2}/\d{1,2}/\d{4})',  # MVR format - driver's license expiry
        r'Expir(?:y|ation)\s*Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'Exp\.?\s*Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'Valid\s*(?:Through|Until)[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
    ]
    for pattern in expiry_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['expiry_date'] = normalize_date(match.group(1))
            print(f" Found License Expiry Date: {data['expiry_date']}")
            # DO NOT override renewal_date here - it should only come from DASH PDF
            # The expiry_date here is the driver's license expiry, not policy renewal
            break
    
    # Date of Birth - MVR format: "Birth Date: 03/02/1980"
    dob_patterns = [
        r'Birth Date:\s*(\d{1,2}/\d{1,2}/\d{4})',  # MVR format
        r'(?:Date\s*of\s*)?Birth\s*Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'DOB[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'Born[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
    ]
    for pattern in dob_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['dob'] = normalize_date(match.group(1))
            print(f" Found DOB: {data['dob']}")
            break
    
    # Issue Date - MVR format: "Issue Date: 16/11/2001"
    issue_patterns = [
        r'Issue Date:\s*(\d{1,2}/\d{1,2}/\d{4})',  # MVR format
        r'Issue\s*Date[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        r'Issued[:\s]+(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})'
    ]
    for pattern in issue_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['issue_date'] = normalize_date(match.group(1))
            print(f" Found Issue Date: {data['issue_date']}")
            break
    
    # License Status - MVR format: "Status: LICENCED"
    status_patterns = [
        r'Status:\s*(LICENCED|LICENSED|VALID|ACTIVE|SUSPENDED|REVOKED|EXPIRED)',  # MVR format
        r'Status[:\s]+(Valid|Suspended|Revoked|Expired)',
        r'License\s*Status[:\s]+(Valid|Suspended|Revoked|Expired)'
    ]
    for pattern in status_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            status = match.group(1).strip().upper()
            # Normalize "LICENCED" to "Valid"
            if status in ['LICENCED', 'LICENSED', 'ACTIVE']:
                data['license_status'] = 'Valid'
            else:
                data['license_status'] = status.capitalize()
            print(f" Found License Status: {data['license_status']}")
            break
    
    # Class/Type - MVR format: "Class: G***"
    class_patterns = [
        r'Class:\s*([A-Z0-9\*]+)',  # MVR format
        r'Class[:\s]+([A-Z0-9]+)',
        r'License\s*Class[:\s]+([A-Z0-9]+)',
        r'Type[:\s]+([A-Z0-9]+)'
    ]
    for pattern in class_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['license_class'] = match.group(1).strip().replace('*', '')
            print(f" Found License Class: {data['license_class']}")
            break
    
    # VIN and Vehicle info: Extract ALL VEHICLES from Policy #1 section (for MVR PDFs)
    # Find Policy #1, then extract up to Policy #2 (or end if no Policy #2)
    policy1_pos = text.find('Policy #1')
    policy1_vehicles_list = []  # Array to store ALL vehicles from Policy #1
    
    print(f"\n[VEHICLES] Searching for 'Policy #1' in MVR...")
    print(f"[VEHICLES] policy1_pos = {policy1_pos}")
    
    if policy1_pos >= 0:
        # Find the NEXT policy number after Policy #1
        remaining_text = text[policy1_pos + len('Policy #1'):]
        next_policy_match = re.search(r'Policy\s*#(\d+)', remaining_text)
        
        if next_policy_match:
            # Policy #1 section ends where the next policy begins
            next_policy_pos = policy1_pos + len('Policy #1') + next_policy_match.start()
            policy1_section = text[policy1_pos:next_policy_pos]
            print(f"[VEHICLES] Found next policy, Policy #1 section size: {len(policy1_section)} chars")
        else:
            # No next policy, take the rest of the document
            policy1_section = text[policy1_pos:]
            print(f"[VEHICLES] No next policy found, taking rest of doc. Policy #1 section size: {len(policy1_section)} chars")
        
        # Extract ALL vehicles from Policy #1
        # Format: "Vehicle #N: YEAR MAKE - MODEL VIN"
        # Find all occurrences of "Vehicle #N:" followed by content until next "Vehicle #" or end
        vehicle_pattern = r'Vehicle\s*#(\d+):\s*([^\n]*(?:\n(?!Vehicle\s*#).*)*)'
        vehicle_matches = re.finditer(vehicle_pattern, policy1_section, re.IGNORECASE)
        
        print(f"[VEHICLES] Searching for vehicles with pattern...")
        
        for match in vehicle_matches:
            vehicle_num = match.group(1).strip()
            vehicle_content = match.group(2)
            
            print(f"\n[VEHICLES] Processing Vehicle #{vehicle_num}...")
            print(f"[VEHICLES] Content (first 300 chars): {vehicle_content[:300]}")
            
            # Check if this is a role label (e.g., "Principal Operator", "Named Insured")
            first_line = vehicle_content.split('\n')[0].strip()
            if re.match(r'^(Principal Operator|Named Insured|Self|Spouse|Relationship|Owner)', first_line, re.IGNORECASE):
                print(f"[VEHICLES] Skipping - this is a role assignment, not a vehicle")
                continue
            
            # Try to extract VIN and year/make/model
            vin = None
            year_make_model = None
            
            # Pattern 1: "YEAR MAKE - MODEL VIN" (VIN is 17 chars, on same line)
            match1 = re.search(r'(\d{4}\s+[A-Z]+(?:\s*-\s*[^\-\n]+)?)\s*-\s*([A-HJ-NPR-Z0-9]{17})', vehicle_content, re.IGNORECASE)
            if match1:
                year_make_model = match1.group(1).strip()
                vin = match1.group(2).strip().upper()
                print(f"[VEHICLES] Pattern 1: Found year/make/model: {year_make_model}, VIN: {vin}")
            
            # Pattern 2: "YEAR MAKE - MODEL\nVIN" (VIN on next line)
            if not vin:
                match2 = re.search(r'(\d{4}\s+[A-Z]+(?:\s*-\s*[^\-\n]+)?)\s*\n\s*([A-HJ-NPR-Z0-9]{17})', vehicle_content, re.IGNORECASE)
                if match2:
                    year_make_model = match2.group(1).strip()
                    vin = match2.group(2).strip().upper()
                    print(f"[VEHICLES] Pattern 2: Found year/make/model: {year_make_model}, VIN: {vin}")
            
            # Pattern 3: Extract first meaningful line and look for VIN anywhere
            if not vin:
                lines = [l.strip() for l in vehicle_content.split('\n') if l.strip()]
                for line in lines[:3]:  # Check first 3 lines
                    vin_match = re.search(r'([A-HJ-NPR-Z0-9]{17})', line)
                    if vin_match:
                        vin = vin_match.group(1).strip().upper()
                        year_make_model = line[:vin_match.start()].strip()
                        print(f"[VEHICLES] Pattern 3: Found year/make/model: {year_make_model}, VIN: {vin}")
                        break
            
            if vin and year_make_model:
                # Clean up the year/make/model
                year_make_model = re.sub(r'\s+', ' ', year_make_model)
                year_make_model = year_make_model.rstrip(' -/:').strip()
                
                # Skip if empty
                if year_make_model and len(year_make_model) > 3:
                    policy1_vehicles_list.append({
                        'vehicle_number': vehicle_num,
                        'vin': vin,
                        'year_make_model': year_make_model
                    })
                    print(f"[VEHICLES] [OK] Added Vehicle #{vehicle_num}: {year_make_model} | VIN: {vin}")
                else:
                    print(f"[VEHICLES] Skipped - year/make/model too short: '{year_make_model}'")
            else:
                print(f"[VEHICLES] [WARN] Could not extract VIN or year/make/model for Vehicle #{vehicle_num}")
    else:
        print("[VEHICLES] [ERROR] Policy #1 not found in MVR PDF")
    
    print(f"\n[VEHICLES] FINAL RESULT: {len(policy1_vehicles_list)} vehicles extracted")
    print(f"[VEHICLES] policy1_vehicles_list = {policy1_vehicles_list}")
    
    # Store all vehicles from Policy #1 for frontend to render
    data['policy1_vehicles'] = policy1_vehicles_list
    
    # For backward compatibility, set single vehicle fields (use first vehicle if available)
    if policy1_vehicles_list:
        data['vin'] = policy1_vehicles_list[0]['vin']
        data['vehicle_year_make_model'] = policy1_vehicles_list[0]['year_make_model']
    
    # Demerit Points - MVR format: "Demerit Points: 00"
    points_patterns = [
        r'Demerit Points:\s*(\d+)',  # MVR format
        r'(?:Demerit\s*)?Points?[:\s]+(\d+)',
        r'Point\s*Balance[:\s]+(\d+)',
        r'Total\s*Points[:\s]+(\d+)'
    ]
    for pattern in points_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            data['demerit_points'] = match.group(1)
            print(f" Found Demerit Points: {data['demerit_points']}")
            break
    
    # Conditions/Restrictions - MVR format: "Conditions: */N"
    conditions_patterns = [
        r'Conditions:\s*([^\n]+)',  # MVR format
        r'Conditions?[:\s]+([^\n]+)'
    ]
    for pattern in conditions_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            cond = match.group(1).strip()
            # Skip if it's just */N or similar placeholder
            if cond and cond not in ['*/N', '*', 'N', 'None', 'NONE']:
                data['conditions'] = cond
                print(f" Found Conditions: {data['conditions']}")
            break
    
    # Number of Convictions - MVR format: "***Number of Convictions: 0 ***"
    convictions_pattern = r'\*+\s*Number of Convictions:\s*(\d+)\s*\*+'
    conv_match = re.search(convictions_pattern, text, re.IGNORECASE)
    if conv_match:
        conv_count = int(conv_match.group(1))
        data['convictions_count'] = str(conv_count)
        print(f" Found Convictions Count: {conv_count}")
        
        # If there are convictions, try to extract them
        if conv_count > 0:
            convictions = []
            
            # Find the "DATE CONVICTIONS, DISCHARGES AND OTHER ACTIONS" section (or similar)
            # This section typically contains the actual conviction details
            conv_section = None
            
            # Try to find "DATE CONVICTIONS" or similar header
            conv_header_match = re.search(r'DATE\s+CONVICTIONS.*?\n(.*?)(?=\*{3,}|END OF REPORT|Licence Number|^$)', text, re.IGNORECASE | re.DOTALL | re.MULTILINE)
            
            if conv_header_match:
                conv_section = conv_header_match.group(1)
            else:
                # Fallback: look from the "Number of Convictions" match onwards
                conv_section_start = conv_match.end()
                # Look for the next section header or end of document
                next_section = re.search(r'(?:^\*+|^[A-Z\*]{3,}|END OF REPORT)', text[conv_section_start:], re.MULTILINE | re.IGNORECASE)
                if next_section:
                    conv_section = text[conv_section_start:conv_section_start + next_section.start()]
                else:
                    conv_section = text[conv_section_start:]
            
            print(f"\n=== CONVICTIONS SECTION (First 2000 chars) ===")
            print(conv_section[:2000] if conv_section else "NO SECTION FOUND")
            print(f"=== END CONVICTIONS SECTION ===\n")
            
            # Try multiple patterns to extract conviction details
            # Pattern 1: Standard MVR format - lines with date, offense, and penalties
            # Looking for patterns like:
            # 01/15/2023 Speeding 20+ km/h over limit Fine: $280
            # Or: 01/15/2023 - Speeding...
            # Or: DISOBEY LEGAL SIGN / OFFENCE DATE 2024/12/28 (multi-line format)
            
            conv_detail_patterns = [
                # Date on one line, description on next, OFFENCE DATE on following line
                r'(\d{1,2}/\d{1,2}/\d{4})\s*\n\s*([A-Za-z\s\-\(\)0-9\.&/]+?)\s*\n\s*OFFENCE\s+DATE\s+(\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}/\d{4})',
                # Date + description + OFFENCE DATE on same line
                r'(\d{1,2}/\d{1,2}/\d{4})\s+([A-Za-z\s\-\(\)0-9\.&/]+?)\s+OFFENCE\s+DATE\s+(\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}/\d{4})',
                # Date + description on one line, OFFENCE DATE on next line
                r'(\d{1,2}/\d{1,2}/\d{4})\s+([A-Za-z\s\-\(\)0-9\.&/]+?)\s*\n\s*OFFENCE\s+DATE\s+(\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}/\d{4})',
                # Multi-line format: Description on one line, OFFENCE DATE on next
                r'([A-Za-z\s\-\(\)0-9\.&/]+?)\s*\n\s*OFFENCE\s+DATE\s+(\d{1,2}/\d{1,2}/\d{4})',
                # Date + offense + fine (most common MVR format)
                r'(\d{1,2}/\d{1,2}/\d{4})\s+([A-Za-z\s\-\(\)0-9\.&/]+?)(?:\s+Fine:\s*\$?[\d,.]+|\s+Penalty.*)?(?:\n|$)',
                # Date - description format
                r'(\d{1,2}/\d{1,2}/\d{4})\s*[\-]\s*([A-Za-z\s\-\(\)0-9\.&/]+?)(?:\n|$)',
                # Numbered conviction format: 1. Date Description
                r'^\s*\d+\.\s+(\d{1,2}/\d{1,2}/\d{4})\s+([A-Za-z\s\-\(\)0-9\.&/]+?)$',
                # Conviction list format with newlines separating offense from fine
                r'(\d{1,2}/\d{1,2}/\d{4})\s*\n\s*([A-Za-z\s\-\(\)0-9\.&/]+?)(?=\n\d{1,2}/\d{1,2}/\d{4}|\n---|\n\*|\Z)',
            ]
            
            for pattern in conv_detail_patterns:
                print(f"\n[PATTERN] Trying pattern: {pattern[:80]}...")
                conv_matches = re.finditer(pattern, conv_section, re.IGNORECASE | re.MULTILINE)
                matched_count = 0
                
                for match in conv_matches:
                    # Handle formats with an explicit OFFENCE DATE
                    if match.lastindex == 3:
                        # Format: conviction date, description, offence date
                        description = match.group(2).strip()
                        date_str = match.group(3).strip()
                    elif 'OFFENCE' in pattern or 'OFFENCE' in match.group(0):
                        # Format: description first, offence date second
                        description = match.group(1).strip()
                        date_str = match.group(2).strip()
                    else:
                        # Format: date first, description second
                        date_str = match.group(1).strip()
                        description = match.group(2).strip()
                    
                    # Clean up description - remove extra whitespace and common artifacts
                    description = re.sub(r'\s+', ' ', description)
                    description = re.sub(r'\s*[Ff]ine:\s*\$?[\d,.]+\s*', '', description)
                    description = re.sub(r'\s*[Pp]enalty.*?$', '', description, flags=re.MULTILINE)
                    description = re.sub(r'\s*OFFENCE\s+DATE\s+(\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}/\d{4})', '', description, flags=re.IGNORECASE)
                    description = description.strip()

                    # If OFFENCE DATE is present in the matched text, use it
                    offence_match = re.search(r'OFFENCE\s+DATE\s+(\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}/\d{4})', match.group(0), re.IGNORECASE)
                    if offence_match:
                        date_str = offence_match.group(1).strip()
                    
                    # Skip if description is empty or just punctuation
                    if description and description not in ['', '-', '*', 'N', 'None', 'NONE'] and len(description) > 2:
                        conviction = {
                            'date': date_str,
                            'description': description
                        }
                        # Avoid duplicates
                        if conviction not in convictions:
                            convictions.append(conviction)
                            matched_count += 1
                            print(f"   Found: {conviction['date']} - {conviction['description'][:60]}...")
                
                # If we found offence-date matches, prefer them and stop
                if matched_count > 0 and 'OFFENCE' in pattern.upper():
                    print(f" Pattern matched {matched_count} conviction(s) with OFFENCE DATE, stopping pattern search")
                    break
                # If we found enough convictions with this pattern, use it
                if len(convictions) >= conv_count:
                    print(f" Pattern matched {matched_count} convictions, stopping pattern search")
                    break
                elif matched_count > 0:
                    print(f" Pattern matched {matched_count} conviction(s)")
            
            if convictions:
                data['convictions'] = convictions
                print(f"\n[OK] Extracted {len(convictions)} conviction details out of {conv_count} expected")
                if len(convictions) < conv_count:
                    print(f"[WARNING]  Note: Expected {conv_count} but found {len(convictions)} - PDF format may vary")
            else:
                # If we couldn't extract details, at least show count
                print(f"[WARNING]  Could not extract conviction details (found count: {conv_count})")
                print(f"    This might be due to PDF format variation. Please check the PDF manually.")
    else:
        # Default to 0 if not found
        data['convictions_count'] = '0'
        print(f" No convictions section found (defaulting to 0 convictions)")
    
    print(f"=== MVR EXTRACTED DATA ===")
    print(json.dumps(data, indent=2))
    print(f"=== END DATA ===")
    
    # CRITICAL: Verify policy1_vehicles is in the data being returned
    print(f"\n[VERIFY] About to return extract_mvr_fields data:")
    print(f"[VERIFY] - 'policy1_vehicles' key exists: {'policy1_vehicles' in data}")
    if 'policy1_vehicles' in data:
        print(f"[VERIFY] - policy1_vehicles value: {data['policy1_vehicles']}")
        print(f"[VERIFY] - policy1_vehicles length: {len(data['policy1_vehicles'])}")
    print(f"[VERIFY] - Total data keys: {len(data)}")
    
    return data


def normalize_date(date_str):
    """
    Convert various date formats to MM/DD/YYYY
    """
    try:
        # Try different date formats - Check DD/MM/YYYY first (Canadian format in DASH PDFs)
        # Then fall back to MM/DD/YYYY
        formats = [
            '%d/%m/%Y', '%d-%m-%Y',  # DD/MM/YYYY first (DASH PDF format)
            '%d/%m/%y', '%d-%m-%y',  # DD/MM/YY first (DASH PDF format)
            '%m/%d/%Y', '%m-%d-%Y',  # MM/DD/YYYY (fallback)
            '%m/%d/%y', '%m-%d-%y',  # MM/DD/YY (fallback)
            '%Y-%m-%d', '%Y/%m/%d'   # ISO format
        ]
        
        for fmt in formats:
            try:
                date_obj = datetime.strptime(date_str, fmt)
                return date_obj.strftime('%m/%d/%Y')
            except ValueError:
                continue
        
        return date_str  # Return original if no format matches
    except:
        return date_str


def parse_property_quote_with_vertex_ai(pdf_content):
    """
    Parse Property/Tenant Quote PDF using Vertex AI Gemini model
    Detects quote type (tenant vs property) and applies appropriate extraction
    
    Args:
        pdf_content: Bytes of the PDF file OR file path string
        
    Returns:
        dict: Extracted quote data with success status and quote type
    """
    try:
        print("\n[VERTEX AI] Starting Quote PDF parsing with Gemini...")
        
        # Handle file path string - read the file bytes
        if isinstance(pdf_content, str):
            if os.path.exists(pdf_content):
                with open(pdf_content, 'rb') as f:
                    pdf_content = f.read()
            else:
                raise ValueError(f"File not found: {pdf_content}")
        
        # Initialize Vertex AI
        project_id = os.getenv('GOOGLE_CLOUD_PROJECT')
        location = os.getenv('GOOGLE_CLOUD_LOCATION', 'us-central1')
        
        if not project_id:
            raise ValueError("GOOGLE_CLOUD_PROJECT environment variable not set")
        
        vertexai.init(project=project_id, location=location)
        model = GenerativeModel("gemini-2.0-flash")
        
        # Convert PDF to base64 (not used but kept for potential future use)
        pdf_base64 = base64.b64encode(pdf_content).decode('utf-8')
        pdf_part = Part.from_data(
            data=pdf_content,
            mime_type="application/pdf"
        )
        
        # STEP 1: Detect quote type (tenant vs property)
        print("[VERTEX AI] Step 1: Detecting quote type...")
        
        type_response = model.generate_content([pdf_part, QUOTE_TYPE_DETECTION_PROMPT])
        quote_type = type_response.text.strip().upper()
        
        # Normalize quote type
        valid_types = ["TENANT", "HOMEOWNERS", "RENTED_DWELLING", "CONDO", "RENTED_CONDO", "MULTI_PROPERTY"]
        if quote_type not in valid_types:
            # Map legacy/common variations
            if "MULTI" in quote_type:
                quote_type = "MULTI_PROPERTY"
            elif "TENANT" in quote_type:
                quote_type = "TENANT"
            elif "RENTED" in quote_type and "DWELLING" in quote_type:
                quote_type = "RENTED_DWELLING"
            elif "HOMEOWNER" in quote_type or "PROPERTY" in quote_type or "PRIMARY" in quote_type:
                quote_type = "HOMEOWNERS"
            else:
                quote_type = "HOMEOWNERS"  # Default
        
        print(f"[VERTEX AI] Detected quote type: {quote_type}")
        
        # STEP 2: Extract data using appropriate prompt from schema
        print(f"[VERTEX AI] Step 2: Extracting {quote_type} quote data...")
        
        extraction_prompt = get_extraction_prompt(quote_type)
        
        # Generate response
        response = model.generate_content([pdf_part, extraction_prompt])
        response_text = response.text.strip()
        
        print(f"[VERTEX AI] Received response ({len(response_text)} characters)")
        
        # Clean response - remove markdown code blocks if present
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        # Parse JSON response
        try:
            raw_data = json.loads(response_text)
            print(f"[VERTEX AI SUCCESS] Extracted {len(raw_data)} fields from {quote_type} quote")
            
            # Transform to coverpage format
            data = transform_to_coverpage_format(raw_data, quote_type)
            
            # Validate extraction
            validation = validate_extraction(data, quote_type)
            print(f"[VERTEX AI] Validation: {validation['found_count']}/{validation['total_required']} required fields found")
            if validation['missing_fields']:
                print(f"[VERTEX AI] Missing fields: {validation['missing_fields']}")
            
            # Log important fields based on type
            print(f"[VERTEX AI] Quote Type: {quote_type}")
            print(f"[VERTEX AI] Policy Holder: {data.get('policy_holder_name', 'N/A')}")
            print(f"[VERTEX AI] Building Coverage: {data.get('building_coverage', 'N/A')}")
            print(f"[VERTEX AI] Contents Coverage: {data.get('contents_coverage', 'N/A')}")
            print(f"[VERTEX AI] ALE Coverage: {data.get('ale_coverage', 'N/A')}")
            print(f"[VERTEX AI] Liability Coverage: {data.get('liability_coverage', 'N/A')}")
            print(f"[VERTEX AI] Deductible: {data.get('deductible', 'N/A')}")
            
            # Log water coverages if present
            water_fields = ['water_sewer_backup', 'water_ground_water', 'water_overland_water', 'water_above_ground', 'water_service_lines']
            water_present = [f for f in water_fields if data.get(f)]
            if water_present:
                print(f"[VERTEX AI] Water Coverages: {', '.join(water_present)}")
            
            return {
                "success": True,
                "data": data,
                "raw_data": raw_data,
                "method": "vertex_ai",
                "quote_type": quote_type,
                "validation": validation
            }
        except json.JSONDecodeError as je:
            print(f"[VERTEX AI ERROR] Failed to parse JSON response: {str(je)}")
            print(f"[VERTEX AI] Raw response: {response_text[:500]}...")
            return {
                "success": False,
                "error": f"Failed to parse AI response as JSON: {str(je)}",
                "raw_response": response_text[:1000]
            }
        
    except Exception as e:
        print(f"[VERTEX AI ERROR] Quote parsing failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": f"Vertex AI parsing failed: {str(e)}"
        }


# Prompts are now imported from quote_extraction_schema.py


def parse_property_quote_pdf(pdf_content):
    """
    Parse Property Insurance Quote PDF and extract key coverage information
    Uses Vertex AI if available, falls back to regex-based extraction
    
    Args:
        pdf_content: Bytes of the PDF file
        
    Returns:
        dict: Extracted property quote data with success status
    """
    # Try Vertex AI first if available
    if VERTEX_AI_AVAILABLE:
        try:
            result = parse_property_quote_with_vertex_ai(pdf_content)
            if result['success']:
                return result
            else:
                print("[WARNING] Vertex AI parsing failed, falling back to regex-based extraction")
        except Exception as e:
            print(f"[WARNING] Vertex AI error: {str(e)}, falling back to regex-based extraction")
    
    # Fallback to regex-based extraction
    try:
        print("\n[INFO] Starting Property Quote PDF parsing (regex fallback)...")
        import pdfplumber
        
        if isinstance(pdf_content, bytes):
            pdf_file = BytesIO(pdf_content)
        else:
            pdf_file = pdf_content
            
        full_text = ""
        
        with pdfplumber.open(pdf_file) as pdf:
            print(f"[PDF] PDF has {len(pdf.pages)} pages")
            for page_idx, page in enumerate(pdf.pages):
                try:
                    page_text = page.extract_text()
                    if page_text:
                        full_text += page_text + "\n"
                        print(f"  Page {page_idx + 1}: Extracted {len(page_text)} characters")
                except Exception as page_error:
                    print(f"  [ERROR] Page {page_idx + 1}: {str(page_error)}")
        
        print(f"[OK] Total text extracted: {len(full_text)} characters")
        
        if not full_text or len(full_text.strip()) < 50:
            return {
                "success": False,
                "error": "No text could be extracted from PDF"
            }
        
        # Extract property quote data
        data = extract_property_fields(full_text)
        
        print(f"[SUCCESS] Extracted {len(data)} property quote fields")
        
        return {
            "success": True,
            "data": data,
            "method": "regex_fallback"
        }
        
    except Exception as e:
        print(f"[ERROR] Property quote parsing failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e)
        }


def extract_property_fields(text):
    """
    Extract key fields from Property Quote PDF text
    """
    data = {}
    normalized = re.sub(r"\s+", " ", text).strip()
    
    # Broker Information
    broker_match = re.search(r"Broker:?\s*([A-Z][A-Za-z\s,]+?)(?:\s*Email:|$)", text, re.IGNORECASE)
    if broker_match:
        data["broker_name"] = broker_match.group(1).strip()
    
    email_match = re.search(r"Email:?\s*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", text, re.IGNORECASE)
    if email_match:
        data["broker_email"] = email_match.group(1).strip()
    
    phone_match = re.search(r"(?:Phone|Tel|Telephone):?\s*([\d\s\-\(\)]+)", text, re.IGNORECASE)
    if phone_match:
        phone = re.sub(r"[^\d]", "", phone_match.group(1))
        if len(phone) >= 10:
            data["broker_phone"] = phone_match.group(1).strip()
    
    # Policy Information
    policy_match = re.search(r"(?:Policy|Binder)\s*(?:#|No\.?|Number)?:?\s*([A-Z0-9\-]+)", text, re.IGNORECASE)
    if policy_match:
        data["policy_number"] = policy_match.group(1).strip()
    
    # Effective Date
    effective_match = re.search(r"Effective\s*Date:?\s*([0-9]{1,2}[\/\-][0-9]{1,2}[\/\-][0-9]{2,4})", text, re.IGNORECASE)
    if effective_match:
        data["effective_date"] = normalize_date(effective_match.group(1))
    
    # Insurance Company
    company_patterns = [
        r"(?:Insurance\s*Company|Insurer|Carrier):?\s*([A-Z][A-Za-z\s&]+(?:Insurance|Inc\.?))",
        r"([A-Z][A-Za-z\s&]+Insurance(?:\s+Company)?)",
    ]
    for pattern in company_patterns:
        company_match = re.search(pattern, text, re.IGNORECASE)
        if company_match:
            company = re.sub(r"\s+", " ", company_match.group(1)).strip()
            data["insurance_company"] = company
            break
    
    # Policy Holder
    holder_patterns = [
        r"(?:Policy\s*Holder|Insured|Named\s*Insured):?\s*([A-Z][A-Za-z\s,&]+?)(?:\s*Address:|$)",
        r"Name:?\s*([A-Z][A-Za-z\s,&]+?)(?:\s*Address:|$)",
    ]
    for pattern in holder_patterns:
        holder_match = re.search(pattern, text, re.IGNORECASE)
        if holder_match:
            data["policy_holder"] = holder_match.group(1).strip()
            break
    
    # Property Address
    address_match = re.search(r"(?:Property\s*)?Address:?\s*([0-9]+\s+[A-Za-z0-9\s,.-]+)", text, re.IGNORECASE)
    if address_match:
        data["property_address"] = address_match.group(1).strip()
    
    # Coverage Amounts - Extract from coverage table
    # Format is typically: "Coverage Name $Amount" on separate lines
    # IMPORTANT: Extract amounts for each coverage type separately
    # Note: PDFs often have multiple coverage sections, we extract all of them
    
    # Helper function to extract coverage from a section
    def extract_coverage_from_section(section_text):
        coverage = {}
        
        # Building/Residence
        res_match = re.search(r"^Residence\s+\$\s*([0-9,]+)", section_text, re.IGNORECASE | re.MULTILINE)
        if res_match:
            coverage["building_coverage"] = res_match.group(1).replace(",", "")
        
        # Contents
        cont_match = re.search(r"^Contents\s+\$\s*([0-9,]+)", section_text, re.IGNORECASE | re.MULTILINE)
        if cont_match:
            coverage["contents_coverage"] = cont_match.group(1).replace(",", "")
        
        # Outbuildings
        out_match = re.search(r"^Outbuildings?\s+\$\s*([0-9,]+)", section_text, re.IGNORECASE | re.MULTILINE)
        if out_match:
            coverage["outbuildings_coverage"] = out_match.group(1).replace(",", "")
        
        # Liability
        liab_match = re.search(r"Single\s+Limit\s+\$\s*([0-9,]+)", section_text, re.IGNORECASE)
        if liab_match:
            coverage["liability_coverage"] = liab_match.group(1).replace(",", "")
        
        # Deductible
        ded_match = re.search(r"^Deductible\s+\$\s*([0-9,]+)", section_text, re.IGNORECASE | re.MULTILINE)
        if ded_match:
            coverage["deductible"] = ded_match.group(1).replace(",", "")
        
        # ALE
        ale_match = re.search(r"^Additional\s+Living\s+Expenses?\s+(.+?)$", section_text, re.IGNORECASE | re.MULTILINE)
        if ale_match:
            ale_value = ale_match.group(1).strip()
            if "Inc" in ale_value:
                coverage["ale_coverage"] = "Included"
            elif "$" in ale_value:
                amount_match = re.search(r"\$\s*([0-9,]+)", ale_value)
                if amount_match:
                    coverage["ale_coverage"] = amount_match.group(1).replace(",", "")
        
        return coverage
    
    # Extract ALL coverage occurrences (find all "Residence $X" patterns)
    # Then assign to coverage types based on order
    all_residence_finds = list(re.finditer(r"^Residence\s+\$\s*([0-9,]+)", text, re.IGNORECASE | re.MULTILINE))
    
    # Extract coverage for first occurrence (usually Homeowners if it exists)
    if len(all_residence_finds) >= 1:
        homeowners_section = text[max(0, all_residence_finds[0].start() - 1000):all_residence_finds[0].end() + 1000]
        homeowners_coverage = extract_coverage_from_section(homeowners_section)
        for key, value in homeowners_coverage.items():
            data[f"homeowners_{key}"] = value
            # Also set backwards-compatible keys (for first coverage type)
            data[key] = value
    
    # Extract coverage for second occurrence (usually Rented Dwelling if it exists)
    if len(all_residence_finds) >= 2:
        rented_section = text[max(0, all_residence_finds[1].start() - 1000):all_residence_finds[1].end() + 1000]
        rented_coverage = extract_coverage_from_section(rented_section)
        for key, value in rented_coverage.items():
            data[f"rented_dwelling_{key}"] = value
    
    # Extract coverage for third occurrence (usually Tenant or Condo)
    if len(all_residence_finds) >= 3:
        tenant_section = text[max(0, all_residence_finds[2].start() - 1000):all_residence_finds[2].end() + 1000]
        tenant_coverage = extract_coverage_from_section(tenant_section)
        for key, value in tenant_coverage.items():
            data[f"tenant_{key}"] = value
    
    # Extract coverage for fourth occurrence (Condo)
    if len(all_residence_finds) >= 4:
        condo_section = text[max(0, all_residence_finds[3].start() - 1000):all_residence_finds[3].end() + 1000]
        condo_coverage = extract_coverage_from_section(condo_section)
        for key, value in condo_coverage.items():
            data[f"condo_{key}"] = value
    
    # Also keep backwards compatibility - use Homeowners as default if it exists
    if "homeowners_building_coverage" in data:
        data["building_coverage"] = data["homeowners_building_coverage"]
    if "homeowners_contents_coverage" in data:
        data["contents_coverage"] = data["homeowners_contents_coverage"]
    if "homeowners_outbuildings_coverage" in data:
        data["outbuildings_coverage"] = data["homeowners_outbuildings_coverage"]
    if "homeowners_liability_coverage" in data:
        data["liability_coverage"] = data["homeowners_liability_coverage"]
    if "homeowners_deductible" in data:
        data["deductible"] = data["homeowners_deductible"]
    if "homeowners_ale_coverage" in data:
        data["ale_coverage"] = data["homeowners_ale_coverage"]
    
    # Water Coverages
    if re.search(r"Sewer\s*Back[\s-]?up", normalized, re.IGNORECASE):
        sewer_match = re.search(r"Sewer\s*Back[\s-]?up:?\s*(?:\$?\s*([0-9,]+)|Included)", normalized, re.IGNORECASE)
        if sewer_match:
            data["sewer_backup"] = sewer_match.group(1).replace(",", "") if sewer_match.group(1) else "Included"
    
    if re.search(r"Overland\s*Water", normalized, re.IGNORECASE):
        overland_match = re.search(r"Overland\s*Water:?\s*(?:\$?\s*([0-9,]+)|Included)", normalized, re.IGNORECASE)
        if overland_match:
            data["overland_water"] = overland_match.group(1).replace(",", "") if overland_match.group(1) else "Included"
    
    if re.search(r"Ground\s*Water", normalized, re.IGNORECASE):
        ground_match = re.search(r"Ground\s*Water:?\s*(?:\$?\s*([0-9,]+)|Included)", normalized, re.IGNORECASE)
        if ground_match:
            data["ground_water"] = ground_match.group(1).replace(",", "") if ground_match.group(1) else "Included"
    
    # Endorsements
    if re.search(r"(?:Guaranteed|Building)\s*Replacement\s*Cost", normalized, re.IGNORECASE):
        data["guaranteed_replacement"] = "Included"
    
    if re.search(r"Replacement\s*Cost\s*Contents", normalized, re.IGNORECASE):
        data["replacement_cost_contents"] = "Included"
    
    # Detect Coverage Types (Homeowners, Tenant, Condo, Rented Dwelling)
    # Use BOTH text patterns AND extracted data to determine coverage types
    coverage_types = []
    
    print("\n[COVERAGE DETECTION] Starting coverage type detection...")
    print(f"[COVERAGE DETECTION] Searching text length: {len(text)} chars")
    
    # Store what actual coverage data we extracted
    has_building = "building_coverage" in data and data["building_coverage"]
    has_dwelling = "dwelling_coverage" in data and data["dwelling_coverage"]
    has_residence = "residence_limit" in data and data["residence_limit"]
    has_contents = "contents_coverage" in data and data["contents_coverage"]
    has_liability = "liability_coverage" in data and data["liability_coverage"]
    has_outbuildings = "outbuildings_coverage" in data and data.get("outbuildings_coverage")
    
    print(f"[COVERAGE DATA] Building: {has_building}, Dwelling: {has_dwelling}, Residence: {has_residence}, Contents: {has_contents}, Liability: {has_liability}, Outbuildings: {has_outbuildings}")
    
    # STRATEGY 1: SECTION HEADER DETECTION (Most Reliable)
    # Look for actual section headings that indicate coverage sections
    # Real patterns from PDF quotes: "Primary - Homeowners", "Rented Dwelling", etc.
    print("\n[COVERAGE DETECTION] Strategy 1: Looking for section headers...")
    
    # Homeowners - look for "Primary - Homeowners" pattern
    if re.search(r"Primary\s*-\s*Homeowners?(?:\s+\(Protected\))?", text, re.IGNORECASE):
        coverage_types.append("Homeowners")
        print(f"[COVERAGE DETECTION] [OK] SECTION HEADER: 'Primary - Homeowners' found")
    
    # Tenant - look for "Tenant" as a section header (not in the middle of a sentence with commas)
    if re.search(r"^\s*(?:\d+\s+of\s+\d+\s*\|\s*)?Tenant(?:\s+\(Protected\))?", text, re.IGNORECASE | re.MULTILINE):
        coverage_types.append("Tenant")
        print(f"[COVERAGE DETECTION] [OK] SECTION HEADER: 'Tenant' found as section")
    
    # Condo - look for "Condo" or "Condominium" as a section header
    if re.search(r"^\s*(?:\d+\s+of\s+\d+\s*\|\s*)?(?:Condo|Condominium)(?:\s+\(Protected\))?", text, re.IGNORECASE | re.MULTILINE):
        coverage_types.append("Condo")
        print(f"[COVERAGE DETECTION] [OK] SECTION HEADER: 'Condo' found as section")
    
    # Rented Dwelling - look for "Rented Dwelling" as a section header
    if re.search(r"^\s*(?:\d+\s+of\s+\d+\s*\|\s*)?Rented\s+Dwelling(?:\s+\(Protected\))?", text, re.IGNORECASE | re.MULTILINE):
        coverage_types.append("Rented Dwelling")
        print(f"[COVERAGE DETECTION] [OK] SECTION HEADER: 'Rented Dwelling' found as section")
    
    # Alternative patterns for Homeowners if standard pattern didn't match
    if "Homeowners" not in coverage_types:
        if re.search(r"^\s*(?:\d+\s+of\s+\d+\s*\|\s*)?.*Homeowners?(?:\s+\(Protected\))?", text, re.IGNORECASE | re.MULTILINE):
            coverage_types.append("Homeowners")
            print(f"[COVERAGE DETECTION] [OK] SECTION HEADER: 'Homeowners' found (alternative pattern)")
    
    # STRATEGY 2: HO CODE DETECTION (if no section headers found)
    if not coverage_types:
        print("[COVERAGE DETECTION] No section headers found, checking HO codes...")
        
        # HO3 = Homeowners, HO4 = Tenant, HO6 = Condo
        if re.search(r"\bHO\s*-?\s*3\b", text, re.IGNORECASE):
            coverage_types.append("Homeowners")
            print(f"[COVERAGE DETECTION] [OK] HO CODE: HO3 (Homeowners) found")
        if re.search(r"\bHO\s*-?\s*4\b", text, re.IGNORECASE):
            coverage_types.append("Tenant")
            print(f"[COVERAGE DETECTION] [OK] HO CODE: HO4 (Tenant) found")
        if re.search(r"\bHO\s*-?\s*6\b", text, re.IGNORECASE):
            coverage_types.append("Condo")
            print(f"[COVERAGE DETECTION] [OK] HO CODE: HO6 (Condo) found")
    
    # STRATEGY 3: DATA-BASED DETECTION (if still nothing found)
    if not coverage_types:
        print("[COVERAGE DETECTION] No section headers or HO codes found, using data-based detection...")
        
        # If has building/residence + contents = Homeowners
        if (has_building or has_dwelling or has_residence) and has_contents:
            coverage_types.append("Homeowners")
            print(f"[COVERAGE DETECTION] [OK] DATA-BASED: Homeowners (has building + contents)")
        # If only contents, no building = Tenant
        elif has_contents and not (has_building or has_dwelling or has_residence):
            coverage_types.append("Tenant")
            print(f"[COVERAGE DETECTION] [OK] DATA-BASED: Tenant (contents only, no building)")
        # If has building only = Homeowners
        elif (has_building or has_dwelling or has_residence):
            coverage_types.append("Homeowners")
            print(f"[COVERAGE DETECTION] [OK] DATA-BASED: Homeowners (building coverage only)")
        else:
            print(f"[COVERAGE DETECTION] No data fields detected to infer coverage type")
    
    print(f"\n[COVERAGE DETECTION] Total coverage types detected: {len(coverage_types)}")
    print(f"[COVERAGE DETECTION] FINAL Coverage types: {coverage_types}")
    
    # CRITICAL: Only set coverage_types if we detected something concrete
    if coverage_types:
        # Remove duplicates while preserving order
        coverage_types = list(dict.fromkeys(coverage_types))
        data["coverage_types"] = coverage_types
        data["policy_type"] = coverage_types[0]  # Set primary type as first detected
        print(f"[COVERAGE DETECTION] [OK] STORED - coverage_types: {coverage_types}")
    else:
        # If still nothing detected, default to empty list (not all types)
        data["coverage_types"] = []
        print(f"[COVERAGE DETECTION] [WARNING] No coverage types could be detected, storing empty list")
    
    return data
