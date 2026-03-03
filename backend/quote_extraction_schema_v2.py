"""
Quote Extraction Schema V2
Simplified schema focused ONLY on coverpage fields

This module defines:
1. Quote type detection prompt
2. Extraction prompts for each quote type
3. Field mapping to coverpage display
"""

# ====================================================================
# QUOTE TYPE DETECTION PROMPT
# ====================================================================

QUOTE_TYPE_DETECTION_PROMPT = """Analyze this insurance quote PDF and determine the EXACT quote type.

RULES FOR DETECTION:
1. TENANT - Person RENTING a property
   - Look for: "Tenant", "Tenants", "Renter" in property type
   - Has Contents coverage but NO Dwelling/Residence coverage
   - NO water protection coverages typically
   
2. CONDO - Person OWNS and LIVES IN a condo unit
   - Look for: "Condo", "Condominium", "Unit Owners", "HO-6"
   - Has Contents coverage but NO Dwelling/Residence coverage
   - May have water protection coverages
   - NOT a rental property (owner-occupied)

3. HOMEOWNERS - Person OWNS and LIVES in a house
   - Look for: "Homeowners", "Homeowner's", "Primary-Homeowners"
   - Has Dwelling/Residence coverage (Coverage A)
   - Has Outbuildings coverage
   - Territory: "Homeowner's Comprehensive"

4. RENTED_DWELLING - Landlord renting out a HOUSE
   - Look for: "Rented Dwelling", "Landlord", "Rental Property" (for a house)
   - Has "Rental Income" coverage
   - Territory: "Landlord Comprehensive"

5. RENTED_CONDO - Landlord renting out a CONDO unit
   - Look for: "Rented Condo", "Rental Condo", or Condo with "Rental" indicators
   - Has Contents coverage but NO Dwelling/Residence coverage
   - Is a rental/investment property (NOT owner-occupied)

IMPORTANT: If the PDF has MULTIPLE properties:
- Check EACH property's type
- If there are 2+ properties (any combination of Homeowners, Rented Dwelling, Condo, Rented Condo), respond with: "MULTI_PROPERTY"
- Examples: Homeowners + Rented Dwelling, Homeowners + Rented Condo, Homeowners + Rented Dwelling + Rented Condo

Respond with ONLY one word:
- "TENANT"
- "CONDO" 
- "HOMEOWNERS"
- "RENTED_DWELLING"
- "RENTED_CONDO"
- "MULTI_PROPERTY"
"""


# ====================================================================
# TENANT EXTRACTION PROMPT (8 fields)
# ====================================================================

TENANT_EXTRACTION_PROMPT = """You are an expert insurance document analyzer. Extract ONLY the following fields from this TENANT insurance quote.

EXTRACT THESE EXACT FIELDS ONLY:
1. contents_coverage - Contents coverage amount (e.g., "$20,000")
2. ale_coverage - Additional Living Expenses amount (e.g., "$8,000")
3. liability_coverage - Personal Insurance/Legal Liability amount (e.g., "$1,000,000")
4. deductible - Main policy deductible (e.g., "$2,500")
5. quote_type - Should be "Tenant"
6. effective_date - Policy effective date in MM/DD/YYYY format
7. insurance_company - Name of insurance company (e.g., "Aviva", "Pembridge")
8. policy_holder_name - Full name of the insured person
9. property_address - Full property address (street, city, province, postal code)

RULES:
- Extract EXACT values as shown in the PDF
- Currency must include $ symbol (e.g., "$20,000", "$1,000,000")
- If a field is not found, set it to null
- Do NOT extract premiums, taxes, voluntary coverages, or discounts
- Return ONLY valid JSON, no additional text

RETURN JSON FORMAT:
{
  "contents_coverage": "$XX,XXX",
  "ale_coverage": "$XX,XXX",
  "liability_coverage": "$X,XXX,XXX",
  "deductible": "$X,XXX",
  "quote_type": "Tenant",
  "effective_date": "MM/DD/YYYY",
  "insurance_company": "Company Name",
  "policy_holder_name": "Full Name",
  "property_address": "Street Address, City, Province, Postal Code"
}

Extract now and return ONLY the JSON object:"""


# ====================================================================
# CONDO EXTRACTION PROMPT (13 fields)
# ====================================================================

CONDO_EXTRACTION_PROMPT = """You are an expert insurance document analyzer. Extract ONLY the following fields from this CONDO insurance quote.

EXTRACT THESE EXACT FIELDS ONLY:

COVERAGE AMOUNTS (4 fields):
1. contents_coverage - Contents coverage amount (e.g., "$50,000")
2. ale_coverage - Additional Living Expenses amount (e.g., "$20,000")
3. liability_coverage - Personal Insurance/Legal Liability amount (e.g., "$1,000,000" or "$2,000,000")
4. deductible - Main policy deductible (e.g., "$1,000")

WATER PROTECTION (5 fields - true/false ONLY):
Look in "Extended Coverages" section:
5. water_sewer_backup - Is "Sewer Backup" or "Sewer Back-up" coverage present? (true/false)
6. water_ground_water - Is "Ground Water" coverage present? (true/false)
7. water_overland_water - Is "Overland Water" or "Surface Water" coverage present? (true/false)
8. water_above_ground - Is "Above Ground Water Damage" coverage present? (true/false)
9. water_service_lines - Is "Service Lines" coverage present? (true/false)

NOTE ON WATER: If coverage shows "N/A" or "Not Available", set to false. If coverage appears with any amount (even Inc.), set to true.

METADATA (5 fields):
10. quote_type - Should be "Condo"
11. effective_date - Policy effective date in MM/DD/YYYY format
12. insurance_company - Name of insurance company (e.g., "Pembridge", "Unica")
13. policy_holder_name - Full name of the insured person
14. property_address - Full property address (street, city, province, postal code)

RULES:
- Currency must include $ symbol
- Water coverages are boolean (true/false) - do NOT extract amounts
- If a field is not found, set amounts to null, booleans to false
- Do NOT extract premiums, taxes, voluntary coverages, or discounts
- Return ONLY valid JSON

RETURN JSON FORMAT:
{
  "contents_coverage": "$XX,XXX",
  "ale_coverage": "$XX,XXX",
  "liability_coverage": "$X,XXX,XXX",
  "deductible": "$X,XXX",
  "water_sewer_backup": true/false,
  "water_ground_water": true/false,
  "water_overland_water": true/false,
  "water_above_ground": true/false,
  "water_service_lines": true/false,
  "quote_type": "Condo",
  "effective_date": "MM/DD/YYYY",
  "insurance_company": "Company Name",
  "policy_holder_name": "Full Name",
  "property_address": "Street Address, City, Province, Postal Code"
}

Extract now and return ONLY the JSON object:"""


# ====================================================================
# HOMEOWNERS EXTRACTION PROMPT (16 fields)
# ====================================================================

HOMEOWNERS_EXTRACTION_PROMPT = """You are an expert insurance document analyzer. Extract ONLY the following fields from this HOMEOWNERS insurance quote.

EXTRACT THESE EXACT FIELDS ONLY:

COVERAGE AMOUNTS (6 fields):
1. building_coverage - Residence/Dwelling coverage amount (e.g., "$330,750")
2. outbuildings_coverage - Outbuildings/Other Structures coverage amount (e.g., "$33,075")
3. contents_coverage - Contents/Personal Property coverage amount (e.g., "$231,525")
4. ale_coverage - Additional Living Expenses amount (e.g., "$66,150")
5. liability_coverage - Personal Insurance/Legal Liability amount (e.g., "$1,000,000" or "$2,000,000")
6. deductible - Main policy deductible (e.g., "$1,000" or "$2,000")

WATER PROTECTION (5 fields - true/false ONLY):
Look in "Extended Coverages" section:
7. water_sewer_backup - Is "Sewer Backup" coverage present? (true/false)
8. water_ground_water - Is "Ground Water" coverage present? (true/false)
9. water_overland_water - Is "Overland Water" coverage present? (true/false)
10. water_above_ground - Is "Above Ground Water Damage" coverage present? (true/false)
11. water_service_lines - Is "Service Lines" coverage present? (true/false)

NOTE ON WATER: If coverage shows "N/A" or "Not Available", set to false. If coverage appears with any amount (even Inc.), set to true.

METADATA (5 fields):
12. quote_type - Should be "Homeowners" or "Primary-Homeowners"
13. effective_date - Policy effective date in MM/DD/YYYY format
14. insurance_company - Name of insurance company (e.g., "Unica Insurance Inc.", "Definity")
15. policy_holder_name - Full name of the insured person
16. property_address - Full property address (street, city, province, postal code)

IMPORTANT MAPPING:
- "Residence" = building_coverage
- "Outbuildings" or "Other Structures" = outbuildings_coverage
- "Contents" or "Personal Property" = contents_coverage
- "Additional Living Expenses" or "ALE" = ale_coverage
- "Personal Insurance" or "Legal Liability" = liability_coverage
- "Deductible" (main, not water) = deductible

RULES:
- Currency must include $ symbol
- Water coverages are boolean (true/false) - do NOT extract amounts
- If a field is not found, set amounts to null, booleans to false
- Do NOT extract premiums, taxes, voluntary coverages, discounts, By-Laws, or water deductibles
- Return ONLY valid JSON

RETURN JSON FORMAT:
{
  "building_coverage": "$XXX,XXX",
  "outbuildings_coverage": "$XX,XXX",
  "contents_coverage": "$XXX,XXX",
  "ale_coverage": "$XX,XXX",
  "liability_coverage": "$X,XXX,XXX",
  "deductible": "$X,XXX",
  "water_sewer_backup": true/false,
  "water_ground_water": true/false,
  "water_overland_water": true/false,
  "water_above_ground": true/false,
  "water_service_lines": true/false,
  "quote_type": "Homeowners",
  "effective_date": "MM/DD/YYYY",
  "insurance_company": "Company Name",
  "policy_holder_name": "Full Name",
  "property_address": "Street Address, City, Province, Postal Code"
}

Extract now and return ONLY the JSON object:"""


# ====================================================================
# RENTED DWELLING EXTRACTION PROMPT (16 fields)
# ====================================================================

RENTED_DWELLING_EXTRACTION_PROMPT = """You are an expert insurance document analyzer. Extract ONLY the following fields from this RENTED DWELLING (Landlord) insurance quote.

EXTRACT THESE EXACT FIELDS ONLY:

COVERAGE AMOUNTS (6 fields):
1. building_coverage - Residence/Dwelling coverage amount (e.g., "$460,950")
2. outbuildings_coverage - Outbuildings/Other Structures coverage amount (e.g., "$92,190")
3. contents_coverage - Contents coverage amount (may be "Inc." or a dollar amount)
4. ale_coverage - Additional Living Expenses amount (may be "Inc.")
5. liability_coverage - Personal Insurance/Legal Liability amount (e.g., "$2,000,000")
6. deductible - Main policy deductible (e.g., "$2,000")

WATER PROTECTION (5 fields - true/false ONLY):
Look in "Extended Coverages" section:
7. water_sewer_backup - Is "Sewer Backup" coverage present? (true/false)
8. water_ground_water - Is "Ground Water" coverage present? (true/false)
9. water_overland_water - Is "Overland Water" coverage present? (true/false)
10. water_above_ground - Is "Above Ground Water Damage" coverage present? (true/false)
11. water_service_lines - Is "Service Lines" coverage present? (true/false)

METADATA (5 fields):
12. quote_type - Should be "Rented Dwelling" or "Landlord"
13. effective_date - Policy effective date in MM/DD/YYYY format
14. insurance_company - Name of insurance company
15. policy_holder_name - Full name of property owner
16. property_address - Full property address (street, city, province, postal code)

IDENTIFICATION: This is a Rented Dwelling if:
- Property type contains "Rented Dwelling" or "Landlord"
- Territory shows "Landlord Comprehensive"
- "Rental Income" coverage is present (ignore the amount)

RULES:
- Currency must include $ symbol
- If Contents or ALE shows "Inc.", use null (landlords often don't insure tenant contents)
- Water coverages are boolean only
- Do NOT extract Rental Income amount, premiums, taxes, or By-Laws
- Return ONLY valid JSON

RETURN JSON FORMAT:
{
  "building_coverage": "$XXX,XXX",
  "outbuildings_coverage": "$XX,XXX",
  "contents_coverage": "$XX,XXX or null",
  "ale_coverage": "$XX,XXX or null",
  "liability_coverage": "$X,XXX,XXX",
  "deductible": "$X,XXX",
  "water_sewer_backup": true/false,
  "water_ground_water": true/false,
  "water_overland_water": true/false,
  "water_above_ground": true/false,
  "water_service_lines": true/false,
  "quote_type": "Rented Dwelling",
  "effective_date": "MM/DD/YYYY",
  "insurance_company": "Company Name",
  "policy_holder_name": "Full Name",
  "property_address": "Street Address, City, Province, Postal Code"
}

Extract now and return ONLY the JSON object:"""


# ====================================================================
# MULTI-PROPERTY EXTRACTION PROMPT
# ====================================================================

MULTI_PROPERTY_EXTRACTION_PROMPT = """You are an expert insurance document analyzer. This quote contains MULTIPLE properties. Extract data for EACH property separately.

CRITICAL: Each property in this multi-property quote has ITS OWN coverage section with ITS OWN coverage amounts. You MUST find and extract the specific coverage values for EACH property by looking at the coverage table/section associated with that property's address.

PROPERTY TYPE IDENTIFICATION:
- "Homeowners" or "Primary-Homeowners" = Owner lives in the property (house)
- "Rented Dwelling" = Owner rents out a HOUSE to tenants (landlord property)
- "Condo" = Owner lives in the condo unit (owner-occupied condo)
- "Rented Condo" = Owner rents out a CONDO unit to tenants (landlord condo)
- "Tenant" = Renter lives in the property

IMPORTANT: If a property is a condo AND is rented out (rental/investment), use "Rented Condo" NOT "Condo".

FOR EACH PROPERTY, EXTRACT FROM THAT PROPERTY'S SPECIFIC SECTION:

COVERAGE AMOUNTS - Look for these in EACH property's coverage table:
1. building_coverage - Residence/Dwelling coverage (Coverage A) - null for Tenant/Condo/Rented Condo
2. outbuildings_coverage - Outbuildings/Detached Structures - null for Tenant/Condo/Rented Condo
3. contents_coverage - Contents/Personal Property coverage
4. ale_coverage - Additional Living Expenses / Loss of Use / Rental Income
5. liability_coverage - Personal Liability / Legal Liability (often $1,000,000 or $2,000,000)
6. deductible - Policy deductible (often $1,000 or $2,500)

WATER PROTECTION (5 fields - true/false):
7. water_sewer_backup - Sewer Backup coverage present?
8. water_ground_water - Ground Water coverage present?
9. water_overland_water - Overland Water coverage present?
10. water_above_ground - Above Ground Water coverage present?
11. water_service_lines - Service Lines coverage present?

METADATA:
12. property_address - Address of this specific property
13. quote_type - One of: "Homeowners", "Rented Dwelling", "Condo", "Rented Condo", "Tenant"

SHARED METADATA (same for all properties):
- effective_date - Policy effective date
- insurance_company - Insurance company name
- policy_holder_name - Customer name

EXTRACTION RULES:
- EACH property has its own coverage amounts - DO NOT use the same values for all properties
- Look for coverage tables/sections that are labeled with or appear near each property address
- If a coverage type doesn't apply (building for condo), set to null
- Extract the actual dollar amounts shown for each property

RETURN JSON FORMAT:
{
  "effective_date": "MM/DD/YYYY",
  "insurance_company": "Company Name",
  "policy_holder_name": "Full Name",
  "properties": [
    {
      "property_address": "Address 1",
      "quote_type": "Homeowners",
      "building_coverage": "$XXX,XXX",
      "outbuildings_coverage": "$XX,XXX",
      "contents_coverage": "$XXX,XXX",
      "ale_coverage": "$XX,XXX",
      "liability_coverage": "$X,XXX,XXX",
      "deductible": "$X,XXX",
      "water_sewer_backup": true/false,
      "water_ground_water": true/false,
      "water_overland_water": true/false,
      "water_above_ground": true/false,
      "water_service_lines": true/false
    },
    {
      "property_address": "Address 2",
      "quote_type": "Rented Condo",
      "building_coverage": null,
      "outbuildings_coverage": null,
      "contents_coverage": "$XX,XXX",
      "ale_coverage": "$XX,XXX",
      "liability_coverage": "$X,XXX,XXX",
      "deductible": "$X,XXX",
      "water_sewer_backup": true/false,
      "water_ground_water": true/false,
      "water_overland_water": true/false,
      "water_above_ground": true/false,
      "water_service_lines": true/false
    }
  ]
}

Extract now and return ONLY the JSON object:"""


# ====================================================================
# EXTRACTION PROMPT MAPPING
# ====================================================================

EXTRACTION_PROMPTS = {
    "TENANT": TENANT_EXTRACTION_PROMPT,
    "CONDO": CONDO_EXTRACTION_PROMPT,
    "HOMEOWNERS": HOMEOWNERS_EXTRACTION_PROMPT,
    "RENTED_DWELLING": RENTED_DWELLING_EXTRACTION_PROMPT,
    "MULTI_PROPERTY": MULTI_PROPERTY_EXTRACTION_PROMPT
}


# ====================================================================
# UTILITY FUNCTIONS
# ====================================================================

def get_extraction_prompt(quote_type):
    """Get the appropriate extraction prompt for a quote type"""
    quote_type_upper = quote_type.upper().replace("-", "_").replace(" ", "_")
    return EXTRACTION_PROMPTS.get(quote_type_upper, HOMEOWNERS_EXTRACTION_PROMPT)


def _get_type_prefix(quote_type_str):
    """Determine the type prefix from a quote type string"""
    qt_lower = quote_type_str.lower().replace("-", "_").replace(" ", "_")
    
    if "tenant" in qt_lower:
        return "tenant_"
    elif "condo" in qt_lower and "rented" in qt_lower:
        return "rented_condo_"
    elif "condo" in qt_lower:
        return "condo_"
    elif "rented" in qt_lower or ("dwelling" in qt_lower and "primary" not in qt_lower):
        return "rented_dwelling_"
    else:  # Default to homeowners
        return "homeowners_"


def _process_single_property(property_data, quote_type):
    """Process a single property's coverage data and return prefixed fields"""
    result = {}
    
    type_prefix = _get_type_prefix(quote_type)
    
    # Coverage mappings
    coverage_map = {
        'building_coverage': 'building_coverage',
        'residence_coverage': 'building_coverage',
        'outbuildings_coverage': 'outbuildings_coverage',
        'contents_coverage': 'contents_coverage',
        'ale_coverage': 'ale_coverage',
        'additional_living_expenses_coverage': 'ale_coverage',
        'liability_coverage': 'liability_coverage',
        'personal_liability_coverage': 'liability_coverage',
        'personal_insurance': 'liability_coverage',
        'deductible': 'deductible'
    }
    
    # Apply coverage mappings WITH type prefix
    for src_field, base_field in coverage_map.items():
        if src_field in property_data and property_data[src_field]:
            value = property_data[src_field]
            # Skip null/N/A values
            if value and str(value).lower() not in ['null', 'n/a', 'none', '']:
                prefixed_field = type_prefix + base_field
                result[prefixed_field] = value
    
    # Water coverage (no prefix, shared across properties)
    water_map = {
        'water_sewer_backup': 'water_sewer_backup',
        'water_ground_water': 'water_ground_water',
        'water_overland_water': 'water_overland_water',
        'water_above_ground': 'water_above_ground',
        'water_service_lines': 'water_service_lines'
    }
    
    for src_field, dst_field in water_map.items():
        if src_field in property_data:
            result[dst_field] = property_data[src_field]
    
    # Property address if available
    if property_data.get('property_address'):
        addr_key = type_prefix.rstrip('_') + '_address'
        result[addr_key] = property_data['property_address']
    
    return result, type_prefix


def transform_to_coverpage_format(extracted_data, quote_type):
    """
    Transform extracted data to the format expected by the coverpage.
    Maps field names and ensures correct structure.
    
    CRITICAL: Coverpage expects fields WITH type prefixes:
    - homeowners_building_coverage, homeowners_contents_coverage, etc.
    - tenant_contents_coverage, tenant_liability_coverage, etc.
    - condo_contents_coverage, condo_liability_coverage, etc.
    - rented_dwelling_building_coverage, etc.
    
    MULTI-PROPERTY: When extracted_data contains "properties" array,
    we process each property and create prefixed fields for all of them.
    """
    result = {}
    detected_types = []
    
    # Check if this is a multi-property quote
    if 'properties' in extracted_data and isinstance(extracted_data['properties'], list):
        print(f"[TRANSFORM] Multi-property quote detected with {len(extracted_data['properties'])} properties")
        
        # Process each property
        for idx, prop in enumerate(extracted_data['properties']):
            prop_type = prop.get('quote_type', 'Homeowners')
            print(f"[TRANSFORM] Processing property {idx+1}: {prop_type}")
            
            prop_result, type_prefix = _process_single_property(prop, prop_type)
            result.update(prop_result)
            detected_types.append(type_prefix.rstrip('_'))
        
        # Also set non-prefixed coverage from first property for backwards compat
        first_prop = extracted_data['properties'][0]
        coverage_fields = ['building_coverage', 'outbuildings_coverage', 'contents_coverage', 
                          'ale_coverage', 'liability_coverage', 'deductible']
        for field in coverage_fields:
            if first_prop.get(field) and str(first_prop.get(field)).lower() not in ['null', 'n/a', 'none', '']:
                result[field] = first_prop[field]
    else:
        # Single property quote - use original logic
        quote_type_lower = quote_type.lower().replace("-", "_").replace(" ", "_")
        type_prefix = _get_type_prefix(quote_type)
        
        prop_result, _ = _process_single_property(extracted_data, quote_type)
        result.update(prop_result)
        detected_types.append(type_prefix.rstrip('_'))
        
        # Also set non-prefixed coverage for backwards compat
        coverage_fields = ['building_coverage', 'outbuildings_coverage', 'contents_coverage', 
                          'ale_coverage', 'liability_coverage', 'deductible']
        for field in coverage_fields:
            if extracted_data.get(field) and str(extracted_data.get(field)).lower() not in ['null', 'n/a', 'none', '']:
                result[field] = extracted_data[field]
    
    # Water coverage defaults (if not already set)
    water_fields = ['water_sewer_backup', 'water_ground_water', 'water_overland_water', 
                    'water_above_ground', 'water_service_lines']
    for field in water_fields:
        if field not in result:
            result[field] = False
    
    # Metadata mappings (no prefix)
    metadata_fields = ['quote_type', 'effective_date', 'insurance_company', 
                      'policy_holder_name', 'customer_name']
    for field in metadata_fields:
        if extracted_data.get(field):
            if field == 'customer_name':
                result['policy_holder_name'] = extracted_data[field]
            else:
                result[field] = extracted_data[field]
    
    # Store detected types for debugging
    result['_detected_types'] = detected_types
    result['_type_prefix'] = detected_types[0] + '_' if detected_types else 'homeowners_'
    
    # Determine coverage types for display - combine all detected types
    coverage_types_set = set()
    for detected in detected_types:
        if 'tenant' in detected:
            coverage_types_set.update(['contents', 'ale', 'liability', 'deductible'])
        elif 'rented_condo' in detected:
            # Rented Condo has same fields as regular Condo (no building/outbuildings)
            coverage_types_set.update(['contents', 'ale', 'liability', 'deductible'])
        elif 'condo' in detected:
            coverage_types_set.update(['contents', 'ale', 'liability', 'deductible'])
        else:  # homeowners or rented_dwelling (houses have building/outbuildings)
            coverage_types_set.update(['residence', 'outbuildings', 'contents', 'ale', 'liability', 'deductible'])
    
    result['coverage_types'] = list(coverage_types_set)
    
    # Check for water coverage
    if any(result.get(f) for f in water_fields):
        result['has_water_coverage'] = True
    
    # Set flags for each detected coverage type
    if 'homeowners' in detected_types:
        result['has_building_coverage'] = True
        result['is_homeowners'] = True
    if 'tenant' in detected_types:
        result['is_tenant'] = True
    if 'condo' in detected_types:
        result['is_condo'] = True
    if 'rented_dwelling' in detected_types:
        result['is_rented_dwelling'] = True
        result['has_rented_dwelling'] = True
    if 'rented_condo' in detected_types:
        result['is_rented_condo'] = True
    
    # For multi-property, set a flag
    if len(detected_types) > 1:
        result['is_multi_property'] = True
        result['property_count'] = len(detected_types)
    
    return result


def validate_extraction(data, quote_type):
    """
    Validate that required fields were extracted.
    Returns validation result with missing fields.
    """
    required_by_type = {
        'tenant': ['contents_coverage', 'ale_coverage', 'liability_coverage', 'deductible'],
        'condo': ['contents_coverage', 'ale_coverage', 'liability_coverage', 'deductible'],
        'homeowners': ['building_coverage', 'contents_coverage', 'ale_coverage', 'liability_coverage', 'deductible'],
        'rented_dwelling': ['building_coverage', 'liability_coverage', 'deductible']
    }
    
    quote_type_key = quote_type.lower().replace("_", " ").replace("-", " ").split()[0]
    required_fields = required_by_type.get(quote_type_key, required_by_type['homeowners'])
    
    missing = []
    found = []
    
    for field in required_fields:
        if data.get(field):
            found.append(field)
        else:
            missing.append(field)
    
    return {
        'valid': len(missing) == 0,
        'missing_fields': missing,
        'found_fields': found,
        'found_count': len(found),
        'total_required': len(required_fields)
    }


# ====================================================================
# COVERPAGE FIELD REFERENCE
# ====================================================================
"""
COVERPAGE FIELDS (for reference):

Coverage Table (defaultRows in propert coverpage.html):
- residence -> building_coverage
- outbuildings -> outbuildings_coverage  
- contents -> contents_coverage
- ale -> ale_coverage
- liability -> liability_coverage
- deductible -> deductible

Water Protection (waterCoverages array):
- water-sb -> water_sewer_backup
- water-gw -> water_ground_water
- water-ow -> water_overland_water
- water-agw -> water_above_ground
- water-sl -> water_service_lines

Coverage Map in JavaScript (coverageMapBase):
'Residence': 'building_coverage',
'Outbuildings': 'outbuildings_coverage',
'Contents': 'contents_coverage',
'Additional Living Expenses': 'ale_coverage',
'Personal Insurance/Legal Liability': 'liability_coverage',
'Legal Liability': 'liability_coverage',
'Deductible': 'deductible'
"""


if __name__ == "__main__":
    print("📋 Quote Extraction Schema V2 - Coverpage Focused")
    print("=" * 60)
    print("\nQuote Types and Fields:")
    print("-" * 40)
    
    field_counts = {
        "TENANT": "8 fields (4 coverages + 4 metadata)",
        "CONDO": "13 fields (4 coverages + 5 water + 4 metadata)",
        "HOMEOWNERS": "15 fields (6 coverages + 5 water + 4 metadata)",
        "RENTED_DWELLING": "15 fields (6 coverages + 5 water + 4 metadata)",
        "MULTI_PROPERTY": "Multiple properties with shared metadata"
    }
    
    for qt, fields in field_counts.items():
        print(f"  ✅ {qt}: {fields}")
    
    print("\nExtraction prompts loaded successfully.")
