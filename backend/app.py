# app.py - FastAPI backend
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import os
import tiktoken
from dotenv import load_dotenv
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import (
    SystemMessage,
    UserMessage,
)
from azure.core.credentials import AzureKeyCredential

load_dotenv()

app = FastAPI(title="Policy Analyzer API")

# Add CORS middleware to allow requests from the React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set up Azure client using environment-based values
endpoint = os.getenv("AZURE_INFERENCE_SDK_ENDPOINT")
model_name = os.getenv("DEPLOYMENT_NAME", "gpt-4o")
api_key = os.getenv("FOUNDRY_API_KEY")

# Policy document configuration
POLICY_FILE_PATH = os.getenv("POLICY_FILE_PATH", "D:/projects/compliance-checker/sample_data/compliance_guidelines.txt")

client = ChatCompletionsClient(
    endpoint=endpoint,
    credential=AzureKeyCredential(api_key or "MISSING_KEY")
)

# Constants
MAX_TOKENS = 50000  # Maximum context window size
ENCODING_NAME = "cl100k_base"  # Encoding for token counting

# Input models
class AnalysisInput(BaseModel):
    sow: str

class AnalysisResult(BaseModel):
    violations: list
    success: bool
    message: str

def count_tokens(text, encoding_name=ENCODING_NAME):
    """Count the number of tokens in a text string"""
    encoding = tiktoken.get_encoding(encoding_name)
    return len(encoding.encode(text))

def chunk_document(document, max_chunk_size, encoding_name=ENCODING_NAME):
    """Split document into chunks of approximately max_chunk_size tokens"""
    encoding = tiktoken.get_encoding(encoding_name)
    tokens = encoding.encode(document)
    chunks = []
    
    for i in range(0, len(tokens), max_chunk_size):
        chunk_tokens = tokens[i:i+max_chunk_size]
        chunks.append(encoding.decode(chunk_tokens))
    
    return chunks

def load_policy_document():
    """Load policy document from local storage"""
    try:
        with open(POLICY_FILE_PATH, 'r', encoding='utf-8') as file:
            return file.read()
    except Exception as e:
        print(f"Error loading policy file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load policy document: {str(e)}")

def analyze_policy(sow_content, policy_chunk):
    """Analyze policy chunk against SOW using LLM"""
    system_prompt = f"""
    You are a compliance analyst. Your task is to examine the Statement of Work (SOW) 
    against company policies to identify any potential violations. 
    List ONLY the specific violations found, if any. If no violations are found, state 'No violations found.'
    
    Here is the Statement of Work:
    {sow_content}
    """
    
    user_prompt = f"""
    Please analyze the SOW against the following company policy:
    
    {policy_chunk}
    
    Return only the violations found. Be specific about which part of the policy is violated by which part of the SOW.
    """
    
    messages = [
        SystemMessage(system_prompt),
        UserMessage(user_prompt)
    ]
    
    try:
        response = client.complete(
            messages=messages,
            model=model_name
        )
        
        if response.choices:
            return response.choices[0].message.content
        else:
            return "No response from model"
    except Exception as e:
        print(f"Error during LLM call: {e}")
        return f"Error: {str(e)}"

@app.post("/analyze", response_model=AnalysisResult)
async def analyze(input_data: AnalysisInput):
    """Analyze SOW against policy guidelines for violations"""
    try:
        sow_content = input_data.sow
        
        # Load policy document from local storage
        policy_content = load_policy_document()
        
        # Count tokens in SOW
        sow_tokens = count_tokens(sow_content)
        print(f"SOW contains {sow_tokens} tokens")
        
        # Calculate available tokens for policy content
        available_tokens = MAX_TOKENS - sow_tokens - 1000  # Reserve 1000 tokens for prompts
        print(f"Available tokens for policy content: {available_tokens}")
        
        if available_tokens <= 0:
            return AnalysisResult(
                violations=[],
                success=False,
                message="SOW is too large to process with the current MAX_TOKENS setting."
            )
        
        # Split policy content into chunks
        policy_chunks = chunk_document(policy_content, available_tokens)
        print(f"Split policy into {len(policy_chunks)} chunks")
        
        # Initialize violations tracker
        all_violations = []
        
        # Process each policy chunk
        for i, chunk in enumerate(policy_chunks):
            print(f"\nProcessing policy chunk {i+1}/{len(policy_chunks)}...")
            
            # Analyze current chunk
            result = analyze_policy(sow_content, chunk)
            
            # Record violations
            if "No violations found" not in result:
                all_violations.append(result)
            
            print(f"Analysis complete for chunk {i+1}")
        
        # Return results
        return AnalysisResult(
            violations=all_violations,
            success=True,
            message=f"Analysis complete. Found {len(all_violations)} policy violation sets."
        )
        
    except Exception as e:
        return AnalysisResult(
            violations=[],
            success=False,
            message=f"Error during analysis: {str(e)}"
        )

@app.post("/analyze-file", response_model=AnalysisResult)
async def analyze_file(
    sow_file: UploadFile = File(...)
):
    """Analyze SOW file against locally stored policy for violations"""
    try:
        sow_content = await sow_file.read()
        
        # Convert bytes to string
        sow_text = sow_content.decode("utf-8")
        
        # Create input model
        input_data = AnalysisInput(sow=sow_text)
        
        # Call the analyze function
        return await analyze(input_data)
        
    except Exception as e:
        return AnalysisResult(
            violations=[],
            success=False,
            message=f"Error processing SOW file: {str(e)}"
        )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    # Create policies directory if it doesn't exist
    os.makedirs(os.path.dirname(POLICY_FILE_PATH), exist_ok=True)
    
    # If policy file doesn't exist, create an empty one
    if not os.path.exists(POLICY_FILE_PATH):
        with open(POLICY_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write("# Default Policy Document\n\nPlease replace this with your actual policy guidelines.")
    
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)