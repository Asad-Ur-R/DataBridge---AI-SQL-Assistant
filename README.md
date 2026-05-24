## DataBridge AI
**AI-Powered Natural Language Database Assistant**

DataBridge AI is an intelligent SQL assistant that allows users to upload CSV files and interact with their database using plain English — no SQL knowledge required.
Built using Python, Streamlit, PostgreSQL, and the Groq API powered by LLaMA 3.3 70B, the system transforms raw CSV files into fully queryable databases with AI-driven analytics, CRUD operations, and SQL learning support.

---

# Features:
-Smart CSV Upload System
-Upload CSV files directly through the web interface
-Automatic PostgreSQL table creation
-Intelligent column type detection
-Dynamic schema generation
-Automatic Data Cleaning Pipeline

**DataBridge AI** automatically cleans uploaded datasets before inserting them into the database.

Supported Cleaning Operations
Currency string normalization
Example: $1,200 → 1200
Percentage conversion
Example: 45% → 0.45
Null-like value handling
Converts:
N/A
NULL
NaN
empty strings
Boolean normalization
Converts:
Yes/No
True/False
1/0
Duplicate row removal
Automatic date parsing
Data type conversion and validation

---

# AI Modes

**QUERY Mode**
Generate and execute SQL SELECT queries using plain English.

Example
Show me the top 10 customers by revenue

Generated SQL:
SELECT customer_name, revenue
FROM sales
ORDER BY revenue DESC
LIMIT 10;

**CRUD Mode**
Perform database modifications safely using natural language.

Supported Operations
INSERT
UPDATE
DELETE
Safety Confirmation Layer

Before executing any destructive operation:

The AI previews affected rows
Requires explicit user confirmation
Prevents accidental data loss

**LEARN Mode**
An educational SQL learning assistant that explains SQL concepts in simple language using real-world analogies.

Example
Explain JOIN in simple words

Output:
A JOIN is like combining information from two spreadsheets using a common column such as Employee ID.
Security Layer

DataBridge AI validates every SQL statement before execution.

Blocked Operations
DROP TABLE
TRUNCATE
SQL injection patterns
Stacked queries
System table access
Unsafe UPDATE/DELETE without WHERE clause
Additional Protections
Query sanitization
SQL validation engine
Confirmation gates for CRUD operations

**Visualization Support**
Interactive visualizations powered by Plotly.
Users can:
Analyze trends
Generate charts
Explore datasets visually

---

# Deployment Architecture
Frontend
Streamlit Cloud
Database
Local PostgreSQL
Neon Serverless PostgreSQL
AI Infrastructure
Groq API
LLaMA 3.3 70B
Rate Limit Handling
Multi-key API rotation system
Automatic fallback mechanism

---

# Tech Stack
Category	Technology
Language	Python 3.11
Frontend	Streamlit
Database	PostgreSQL + Neon
ORM/DB Driver	SQLAlchemy + psycopg2
AI API	Groq API
LLM	LLaMA 3.3 70B
Data Processing	pandas
Visualization	Plotly
Environment Management	python-dotenv

---

# System Workflow
CSV Upload
     ↓
Automatic Data Cleaning
     ↓
PostgreSQL Table Creation
     ↓
Natural Language Prompt
     ↓
AI SQL Generation
     ↓
SQL Security Validation
     ↓
Safe Database Execution
     ↓
Results + Visualizations

---

# Installation
Clone Repository
git clone https://github.com/your-username/databridge-ai.git
cd databridge-ai

Create Virtual Environment
python -m venv venv
Activate Environment
Windows
venv\Scripts\activate
Linux/Mac
source venv/bin/activate

Install Dependencies
pip install -r requirements.txt

Configure Environment Variables
Create a .env file:

DB_HOST=localhost
DB_PORT=5432
DB_NAME=databridge
DB_USER=postgres
DB_PASSWORD=your_password

GROQ_API_KEY_1=your_key
GROQ_API_KEY_2=your_key

# Run Application
streamlit run app.py

---

# Future Improvements
Role-based authentication
Multi-user workspace support
Query history and logging
Dashboard generation
Voice-to-SQL support
AI-generated reports
Export results to Excel/PDF
Database relationship detection

---

# Project Goals
DataBridge AI was built to:
Simplify database interaction
Make data analysis accessible to non-technical users
Reduce dependency on manual SQL writing
Provide safe AI-assisted database operations
Teach SQL concepts interactively

---

# Contributors
Developed by **Asad Ur Rehman**
BS Data Science Student at Sir Syed University of Engineering and Technology

---

# License

This project is licensed under the MIT License.

---

GitHub Support
If you found this project useful:
Star the repository
Fork the project
Share feedback
Contribute improvements
