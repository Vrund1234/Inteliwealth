# Inteliwealth Data Pipeline - Technical Documentation

## Table of Contents
1. [What is Inteliwealth?](#what-is-inteliwealth)
2. [The Medallion Architecture (Bronze, Silver, Gold)](#medallion-architecture)
3. [How Data Flows Through the System](#data-flow)
4. [Understanding Each Layer](#understanding-layers)
5. [Data Schemas Explained](#data-schemas)
6. [How to Add New Schemas](#adding-new-schemas)
7. [Reporting and Analytics](#reporting)
8. [Troubleshooting](#troubleshooting)

---

## What is Inteliwealth?

**Inteliwealth** is a data processing system for managing mutual fund investment information. Think of it like a filing cabinet that:

1. **Receives** raw investment documents (Excel/CSV files from mutual fund companies)
2. **Organizes** the data in a structured, clean way
3. **Combines** related information into useful business reports
4. **Displays** everything on a dashboard for analysis

The system is designed to handle data from two major Indian mutual fund sources:
- **CAMS** (Computer Age Management Services)
- **KFIN** (KFintech, another leading fund administrator)

Since both companies use different naming conventions and formats, Inteliwealth standardizes everything into a common format for easy analysis.

---

## Medallion Architecture

### The Three-Layer Concept

Imagine you're organizing a messy kitchen:
1. **Bronze Layer** = Raw ingredients piled on the counter (just received, unchanged)
2. **Silver Layer** = Cleaned and sorted ingredients, ready to cook (cleaned, validated, standardized)
3. **Gold Layer** = Finished dishes ready to serve (business-ready reports and insights)

Each layer builds upon the previous one, becoming more refined and useful:

```
Raw Files (Excel/CSV)
        ↓
    [BRONZE LAYER]
    Raw Data Dump
    - Exact copy from source
    - Marked with duplicates
    - No changes applied
        ↓
    [SILVER LAYER]
    Cleaned & Standardized
    - Remove duplicates
    - Fix data types
    - Standardize values
    - Add missing data
        ↓
    [GOLD LAYER]
    Business-Ready Reports
    - Grouped by customer
    - Aggregated by fund
    - Final analytics tables
    - Dashboard-ready data
        ↓
    Dashboard Display
    (User sees clean reports)
```

### Why Three Layers?

**Safety**: Each layer is a checkpoint. If something goes wrong, we can trace back to find where the problem started.

**Flexibility**: Different teams can use data from different layers:
- Data engineers use Bronze for raw analysis
- Analysts use Silver for cleanup and validation
- Business users use Gold for final reports

**Traceability**: We always know where each number came from and how it was transformed.

---

## How Data Flows Through the System

### Step 1: User Uploads Files (Frontend)

A user opens the Inteliwealth dashboard (a web app called Streamlit) and uploads an Excel file. The file contains investment account information, transactions, or SIP (Systematic Investment Plan) details.

```
User → Dashboard → "Upload Files" Button → Selects Excel/CSV File
```

The dashboard automatically recognizes what type of file it is by looking at the filename:

**CAMS Files:**
- `*r9.csv` → Investor Master (account holder information)
- `*r2.csv` → Transaction Master (buy/sell records)
- `*r49.csv` → SIP Master (recurring investment plans)

**KFIN Files:**
- `*mfsd211*` → Investor Master
- `*mfsd201*` → Transaction Master
- `*mfsd243*` → SIP Master

### Step 2: Raw Data Extraction (Bronze Layer)

When the user clicks "Extract Raw Data," the system:

1. **Reads the file** in multiple formats (handles different encodings, compressed files, special characters)
2. **Maps columns** to standard names (CAMS column "INV_NAME" becomes standard "investor_name")
3. **Cleans problematic data** (removes special characters, fixes numbers that look like dates)
4. **Checks for duplicates** (uses a "flag" column: flag=0 means new, flag=1 means duplicate)
5. **Stores everything** in the Bronze database schema

Example: A file from CAMS has column "INV_NAME", a file from KFIN has "INVESTOR_NAME". The system translates both to "investor_name" in Bronze.

```python
CAMS File Column: INV_NAME
                  ↓
             Mapping Logic
                  ↓
        Standard Bronze Schema: investor_name
```

### Step 3: Transformation & Standardization (Silver Layer)

After Bronze data is loaded, the user clicks "Transform Data." The system:

1. **Fetches only new records** (where flag=0, ignoring duplicates)
2. **Standardizes codes** (e.g., "SERVICE" occupation → occupation_id=1)
3. **Validates data types** (ensures phone numbers are actual numbers, dates are actual dates)
4. **Cleans up values** (standardizes state codes, fixes whitespace, standardizes account types like "SAV"→"Savings")
5. **Removes duplicates** again at this layer for extra safety
6. **Appends unique records** to the Silver database schema

### Step 4: Business Model Creation (Gold Layer)

Finally, `load_gold()` runs, which:

1. **Extracts** relevant data from Silver
2. **Groups and aggregates** data by business concepts (customers, funds, schemes)
3. **Creates specialized tables** for each business entity
4. **Optimizes for reporting** by denormalizing and pre-calculating summaries

For example:
- **Investor Master** → **Clients Table** (unique customers with contact info)
- **Transaction Master** → **Transactions Table** (clean transaction records)
- **Transactions + Schemes** → **Holdings Table** (how many units of each fund does each customer own)

---

## Understanding Each Layer

### Bronze Layer (Raw Data)

**Purpose**: Store raw data exactly as received.

**What it contains**:
- `bronze.investor_master` - Account holder information
- `bronze.transaction_master` - Buy/sell transactions
- `bronze.sip_master` - Recurring investment plan details
- `bronze.state_code` - Reference data for states
- `bronze.occupation_code` - Reference data for occupations

**Key feature**: Every record has a `flag` column:
- `flag = 0` → New record (first time seeing it)
- `flag = 1` → Duplicate (we've seen this exact record before)

**Example data in Bronze:**

| folio_no | investor_name | state | occupation | flag |
|----------|---------------|-------|-----------|------|
| ABC123 | Rajesh Kumar | MH | SERVICE | 0 |
| ABC124 | Priya Sharma | DL | SERVICE | 0 |
| ABC123 | Rajesh Kumar | MH | SERVICE | 1 |

(The third row is a duplicate of the first - same person, same address, flagged as 1)

---

### Silver Layer (Cleaned Data)

**Purpose**: Store cleaned, standardized data ready for analysis.

**What it contains**:
- `silver.investor_master` - Cleaned account holder data
- `silver.transaction_master_new` - Cleaned transactions
- `silver.sip_master` - Cleaned SIP records

**Key differences from Bronze**:
- Duplicates removed (only flag=0 records)
- Standardized occupations (strings converted to IDs)
- Standardized states (abbreviations standardized)
- Consistent data types (all dates are actual dates, not text)
- Removed special characters and fixed encoding issues

**Example data in Silver:**

| folio_no | investor_name | state | state_code | occupation_id | occupation_description |
|----------|---------------|-------|-----------|---------------|----------------------|
| ABC123 | Rajesh Kumar | MAHARASHTRA | 27 | 1 | SERVICE |
| ABC124 | Priya Sharma | DELHI | 7 | 1 | SERVICE |

(Notice: "MH" became "MAHARASHTRA", occupation became an ID, state got a standardized code)

---

### Gold Layer (Business Reports)

**Purpose**: Create business-ready tables optimized for analytics and reporting.

**What it contains** (the main Gold tables):

#### 1. **gold.clients**
Unique investors/customers with their contact information.

| client_id | pan_no | email | phone | address | state |
|-----------|--------|-------|-------|---------|-------|
| 1001 | AAAA0000A | rajesh@email.com | 9876543210 | 123 MG Road | MAHARASHTRA |
| 1002 | BBBB0000B | priya@email.com | 9876543211 | 456 CP Road | DELHI |

#### 2. **gold.scheme**
Unique mutual fund schemes offered by different companies.

| scheme_id | amc_id | scheme_code | scheme_name | fund_type |
|-----------|--------|-------------|------------|-----------|
| 1 | 1 | 001 | HDFC Growth Fund | Growth |
| 2 | 2 | 002 | ICICI Balanced Fund | Balanced |

#### 3. **gold.amc** (Asset Management Company)
Company names and details.

| amc_id | amc_code | amc_name |
|--------|----------|----------|
| 1 | HDFC | HDFC Asset Management |
| 2 | ICICI | ICICI Prudential |

#### 4. **gold.holdings**
Current units held by each customer in each scheme.

| client_id | scheme_id | units | nav | market_value | last_updated |
|-----------|-----------|-------|-----|--------------|--------------|
| 1001 | 1 | 100.5 | 1250.50 | 125675.25 | 2024-08-10 |
| 1001 | 2 | 50.25 | 560.00 | 28140.00 | 2024-08-10 |

(Rajesh owns 100.5 units of HDFC Growth Fund and 50.25 units of ICICI Balanced Fund)

#### 5. **gold.transactions**
Clean transaction history (purchases, sales, dividends, etc.).

| transaction_id | client_id | scheme_id | transaction_type | amount | units | date |
|---|---|---|---|---|---|---|
| 1 | 1001 | 1 | BUY | 125000 | 100.5 | 2023-01-15 |
| 2 | 1001 | 1 | DIVIDEND | 2500 | 0 | 2023-06-30 |

#### 6. **gold.sip**
Systematic Investment Plans (recurring investments).

| sip_id | client_id | scheme_id | monthly_amount | start_date | end_date | status |
|--------|-----------|-----------|---|---|---|---|
| 1 | 1001 | 2 | 5000 | 2023-02-01 | 2025-01-31 | ACTIVE |

#### 7. **gold.folio_nominees**
Beneficiary information for each account.

| folio_no | nominee_name | nominee_relation | nominee_percentage |
|----------|--------------|------------------|-------------------|
| ABC123 | Rohit Kumar | SON | 100 |
| ABC124 | Deepa Sharma | DAUGHTER | 50 |

---

## Data Schemas Explained

### What is a Schema?

Think of a schema like a **template for a form**. Just as a bank form has specific fields (Name, Address, Account Number), a schema defines what information goes into a table.

### Available Schemas

#### 1. **Investor Master Schema**
What information do we collect about each investor/account holder?

**Core Fields**:
- `folio_no` - Unique account number
- `investor_name` - Primary account holder's name
- `joint_name_1, joint_name_2` - Joint account holders (if any)
- `pan_no` - Tax ID for primary
- `joint1_pan, joint2_pan` - Tax IDs for joint holders

**Contact**:
- `email` - Email address
- `mobile_no` - Mobile phone
- `phone_res` - Residential phone
- `phone_off` - Office phone

**Address** (multiple parts to capture full address):
- `address1, address2, address3` - Address lines
- `city` - City name
- `state` - State code
- `country` - Country
- `pincode` - Postal code

**Bank Account**:
- `bank_name` - Bank name
- `bank_account_no` - Account number
- `account_type` - Savings, Current, etc.
- `branch` - Branch location
- `ifsc_code` - Bank branch code

**Nominee Information** (up to 3 nominees):
- `nominee1_name, nominee1_relation, nominee1_percentage`
- `nominee2_name, nominee2_relation, nominee2_percentage`
- `nominee3_name, nominee3_relation, nominee3_percentage`

**Other**:
- `dob` - Date of birth
- `holding_nature` - Individual, Joint, etc.
- `tax_status` - Resident, Non-Resident, etc.
- `occupation` - Occupation/profession
- `ckyc_no` - KYC compliance number

---

#### 2. **Transaction Master Schema**
What information about each transaction (buy, sell, dividend)?

**Identifiers**:
- `folio_no` - Which account
- `amc_code` - Which mutual fund company
- `scheme_code` - Which fund/scheme
- `prodcode` - Product identifier

**Transaction Details**:
- `transaction_type` - BUY, SELL, DIVIDEND, SWITCH, etc.
- `transaction_date` - When did it happen
- `amount` - Money involved (rupees)
- `units` - Number of units bought/sold

**Pricing**:
- `nav` - Net Asset Value (price per unit on transaction date)
- `rate` - Exchange rate (if international)

**Additional**:
- `remarks` - Transaction notes
- `status` - PROCESSED, PENDING, CANCELLED, etc.

---

#### 3. **SIP Master Schema**
What are Systematic Investment Plans (monthly recurring investments)?

**Basic Info**:
- `folio_no` - Which account
- `amc_code` - Which company
- `scheme_code` - Which fund

**SIP Details**:
- `sip_amount` - Monthly investment amount
- `sip_start_date` - When SIP started
- `sip_end_date` - When SIP will end
- `frequency` - Monthly, Quarterly, etc.
- `sip_status` - ACTIVE, PAUSED, COMPLETED, etc.

---

#### 4. **Scheme Master Schema**
What are the mutual fund schemes available?

**Identifiers**:
- `scheme_code` - Unique code for scheme
- `amc_code` - Which company offers it
- `isin_code` - International Security Identification Number

**Details**:
- `scheme_name` - Full name of scheme
- `scheme_type` - Growth, Balanced, Debt, Liquid, etc.
- `fund_category` - Large Cap, Mid Cap, Small Cap, etc.
- `launch_date` - When scheme was launched
- `status` - OPEN, CLOSED, etc.

---

#### 5. **State Code Reference Schema**
Mapping table for Indian states and union territories.

| state_code | state_name | gst_code |
|-----------|-----------|----------|
| 01 | ANDHRA PRADESH | 37 |
| 27 | MAHARASHTRA | 27 |
| 07 | DELHI | 07 |

---

#### 6. **Occupation Code Reference Schema**
Mapping table for occupation categories.

| occupation_code | occupation_description | category |
|-----------------|----------------------|----------|
| 1 | SERVICE | EMPLOYED |
| 2 | BUSINESS | SELF_EMPLOYED |
| 3 | STUDENT | UNEMPLOYED |
| 4 | HOUSEWIFE | UNEMPLOYED |
| 5 | RETIRED | RETIRED |

---

## How to Add New Schemas

### Scenario: Adding a "Fee Structure" Schema

Imagine you want to track how much commission each AMC pays for transactions. Here's how to add a new Gold schema:

### Step 1: Design the Schema (Understand What You Need)

First, decide what information you need to track:

```
Fee Structure Table should have:
- amc_code (which company)
- product_code (which product)
- transaction_type (buy, sell, switch, etc.)
- fee_percentage (how much they charge)
- effective_from (when this rate applies)
- effective_to (when this rate stops)
```

### Step 2: Create the Database Table

Write a SQL script (`sql_scripts/fee_structure.sql`):

```sql
CREATE TABLE gold.fee_structure (
    id SERIAL PRIMARY KEY,
    amc_code VARCHAR(50),
    product_code VARCHAR(50),
    transaction_type VARCHAR(20),
    fee_percentage DECIMAL(5, 2),
    effective_from DATE,
    effective_to DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_fee_amc_product ON gold.fee_structure(amc_code, product_code);
```

### Step 3: Create the ETL Script

Create `python_scripts/etl_gold_fee_structure.py`:

```python
import pandas as pd
from utils.db import engine

def get_last_processed_time():
    """Get the timestamp of the last successful load"""
    try:
        result = pd.read_sql(
            "SELECT MAX(created_at) as last_time FROM gold.fee_structure", 
            engine
        )
        last_time = result.iloc[0]["last_time"]
        return pd.Timestamp("1900-01-01") if pd.isna(last_time) else last_time
    except:
        return pd.Timestamp("1900-01-01")

def extract_fees():
    """Extract fee data from Silver layer"""
    last_time = get_last_processed_time()
    
    query = f"""
    SELECT 
        amc_code,
        product_code,
        transaction_type,
        fee_percentage,
        effective_from,
        effective_to
    FROM silver.fee_master
    WHERE created_at > '{last_time}'
    """
    
    return pd.read_sql(query, engine)

def transform_fees(df):
    """Clean and validate fee data"""
    if df.empty:
        return pd.DataFrame()
    
    # Clean AMC code (remove whitespace, make uppercase)
    df["amc_code"] = df["amc_code"].fillna("").str.strip().str.upper()
    
    # Ensure fee_percentage is numeric and between 0-100
    df["fee_percentage"] = pd.to_numeric(df["fee_percentage"], errors="coerce")
    df = df[(df["fee_percentage"] >= 0) & (df["fee_percentage"] <= 100)]
    
    # Validate dates
    df["effective_from"] = pd.to_datetime(df["effective_from"], errors="coerce")
    df["effective_to"] = pd.to_datetime(df["effective_to"], errors="coerce")
    
    # Remove duplicates
    df = df.drop_duplicates()
    
    return df

def load_fees(df):
    """Load cleaned data into Gold schema"""
    if df.empty:
        print("No new fee data to load")
        return True
    
    try:
        df.to_sql(
            name="fee_structure",
            con=engine,
            schema="gold",
            if_exists="append",
            index=False
        )
        print(f"Loaded {len(df)} fee records")
        return True
    except Exception as e:
        print(f"Error loading fees: {e}")
        return False

def main():
    """Main ETL function"""
    try:
        # Step 1: Extract
        raw_df = extract_fees()
        if raw_df.empty:
            print("No new fee data found")
            return
        
        # Step 2: Transform
        clean_df = transform_fees(raw_df)
        
        # Step 3: Load
        load_fees(clean_df)
        
    except Exception as e:
        print(f"Fee structure ETL failed: {e}")
```

### Step 4: Add to Gold Loader

Edit `python_scripts/gold_loader.py` and add:

```python
# At the top with other imports:
from etl_gold_fee_structure import extract_fees, transform_fees, load_fees

# Inside load_gold() function, add:
# =====================================================
# GOLD FEE STRUCTURE
# =====================================================
try:
    print("\nLoading Gold Fee Structure")
    raw_df = extract_fees()
    if not raw_df.empty:
        clean_df = transform_fees(raw_df)
        if load_fees(clean_df):
            print("Fee structure loaded successfully")
    else:
        print("No new fee data found")
except Exception as e:
    print(f"Gold Fee structure failed: {e}")
```

### Step 5: Test the New Schema

1. Ensure your Silver layer has `fee_master` table with fee data
2. Run the transform/load sequence through the dashboard
3. Check if data appears in `gold.fee_structure`

### Important Notes When Adding New Schemas:

1. **Always use incremental loading** - Track the last processed timestamp to avoid reprocessing
2. **Clean your data** - Remove whitespace, validate types, check ranges
3. **Handle duplicates** - Use `.drop_duplicates()` before loading
4. **Add to orchestrator** - Add your ETL to `gold_loader.py` so it runs automatically
5. **Update the dashboard** - Add a preview in `app.py` to see results

---

## Reporting and Analytics

### What Can You Report From Gold Layer?

Since Gold layer has clean, aggregated data, you can easily create reports:

#### 1. **Customer Analysis**
- How many active customers?
- Which states have most customers?
- Average portfolio size per customer?

#### 2. **Fund Performance**
- Which schemes are most popular?
- How much total AUM (Assets Under Management) in each scheme?
- What's the net inflow/outflow each month?

#### 3. **Transaction Analysis**
- Total buy/sell value by AMC?
- Which schemes have highest trading activity?
- Average transaction size?

#### 4. **SIP Analysis**
- How many active SIPs?
- What's the monthly SIP inflow?
- Which schemes have highest SIP enrollment?

#### 5. **Portfolio Analytics**
- Average holdings per customer?
- Most common portfolio types?
- Concentration analysis (how much in one scheme)?

### Example Report Queries

```sql
-- How many customers per state?
SELECT state, COUNT(DISTINCT client_id) as customer_count
FROM gold.clients
GROUP BY state
ORDER BY customer_count DESC;

-- Top 5 schemes by AUM
SELECT scheme_name, SUM(market_value) as total_aum
FROM gold.holdings h
JOIN gold.scheme s ON h.scheme_id = s.scheme_id
GROUP BY scheme_name
ORDER BY total_aum DESC
LIMIT 5;

-- Monthly SIP collection
SELECT DATE_TRUNC('month', date) as month, SUM(amount) as sip_inflow
FROM gold.transactions
WHERE transaction_type = 'SIP'
GROUP BY DATE_TRUNC('month', date);
```

---

## Troubleshooting

### Problem: "0 files processed"

**Possible Causes**:
1. File name doesn't match expected pattern
2. File encoding is corrupted
3. File is empty

**Solution**:
- Check file name matches pattern: `*r9.csv` or `*mfsd211*`
- Try opening file in Excel to verify it's readable
- Check file size is not 0

### Problem: "Data appears in Bronze but not Silver"

**Possible Causes**:
1. Flag=1 (all records marked as duplicates)
2. Data validation failed in transform

**Solution**:
- Check `bronze` table: should have some rows with flag=0
- Look at Streamlit error messages for specific validation errors
- Check state codes match reference data

### Problem: "Holdings show 0 units"

**Possible Causes**:
1. No transaction data in Silver layer
2. Calculation error in Holdings ETL

**Solution**:
- Verify transactions exist: `SELECT COUNT(*) FROM silver.transaction_master_new`
- Check Holdings script (`etl_gold_holdings.py`) for calculation logic

### Problem: "Dashboard is slow"

**Possible Causes**:
1. Too much data in Gold tables
2. Missing database indexes
3. Inefficient queries

**Solution**:
- Archive old data periodically
- Run database maintenance: `VACUUM ANALYZE`
- Check for missing indexes on foreign key columns

### Problem: "Can't connect to database"

**Possible Causes**:
1. Database credentials wrong
2. Database server is down
3. Network issue

**Solution**:
- Check `utils/db.py` for connection string
- Verify database credentials in environment variables
- Ping database server to check connectivity

---

## Architecture Decision Log

### Why Medallion (3-Layer) Architecture?

**Alternative Approaches Considered**:
1. **Direct transformation** (Raw → Gold directly)
   - Problem: No safety checkpoint, hard to debug
   - Problem: Can't audit what changed

2. **Single layer** (everything in one table)
   - Problem: Can't distinguish clean from raw data
   - Problem: Duplicates create confusion

3. **Many layers** (5+ layers)
   - Problem: Complexity without benefit
   - Problem: Slow data flow

**Why 3 layers work best**:
- Balances safety with simplicity
- Each layer has clear responsibility
- Easy to debug (know where to check)
- Aligns with industry best practices

---

## Key Definitions

**Bronze Layer**: Raw data dump as received from source, nothing changed

**Silver Layer**: Cleaned, deduplicated, standardized data ready for analysis

**Gold Layer**: Business-optimized tables for reporting and analytics

**Schema**: Template defining structure (columns) of a table

**ETL**: Extract (get data) → Transform (clean/standardize) → Load (save to database)

**Flag column**: Indicator showing if record is duplicate (flag=1) or new (flag=0)

**AMC**: Asset Management Company (mutual fund provider)

**SIP**: Systematic Investment Plan (automatic recurring investment)

**NAV**: Net Asset Value (price per unit of a mutual fund)

**Folio**: Account number at the mutual fund company

**Holding**: Units of a fund held by a customer

**Nominee**: Designated beneficiary for the account

---

## Summary

The Inteliwealth pipeline is a three-layer data system that receives raw mutual fund data, cleans it, and creates business-ready reports. Think of it like a factory:

- **Receiving Area (Bronze)**: Raw materials as delivered
- **Processing Floor (Silver)**: Materials cleaned and sorted
- **Product Assembly (Gold)**: Final products ready for customers

By following this structure, data is reliable, traceable, and useful for decision-making.
