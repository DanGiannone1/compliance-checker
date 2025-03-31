# policy_analyzer.py
# -----------------------------------------
# Implementation of policy analysis workflow using Azure AI Inference SDK

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

# Set up client using environment-based values
endpoint = os.getenv("AZURE_INFERENCE_SDK_ENDPOINT")
model_name = os.getenv("DEPLOYMENT_NAME", "gpt-4o")
api_key = os.getenv("FOUNDRY_API_KEY")

client = ChatCompletionsClient(
    endpoint=endpoint,
    credential=AzureKeyCredential(api_key or "MISSING_KEY")
)

# Constants
MAX_TOKENS = 50000  # Maximum context window size
ENCODING_NAME = "cl100k_base"  # Encoding for token counting (appropriate for GPT-4)

def count_tokens(text, encoding_name=ENCODING_NAME):
    """Count the number of tokens in a text string"""
    encoding = tiktoken.get_encoding(encoding_name)
    return len(encoding.encode(text))

def load_document(file_path):
    """Load document content from a file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
        return ""

def chunk_document(document, max_chunk_size, encoding_name=ENCODING_NAME):
    """Split document into chunks of approximately max_chunk_size tokens"""
    encoding = tiktoken.get_encoding(encoding_name)
    tokens = encoding.encode(document)
    chunks = []
    
    for i in range(0, len(tokens), max_chunk_size):
        chunk_tokens = tokens[i:i+max_chunk_size]
        chunks.append(encoding.decode(chunk_tokens))
    
    return chunks

def analyze_policy(sow_content, policy_chunk):
    """
    Analyze a chunk of policy content against the SOW using LLM
    Returns the LLM's analysis of policy violations
    """
    system_prompt = f"""
    You are a compliance analyst. Your task is to examine the Statement of Work (SOW) 
    against company policies to identify any potential violations. 
    First provide your thought process on the comparison. Then give a final answer that is either "no violations found" or the violation you found.
    
    Here is the Statement of Work:
    {sow_content}
    """
    
  
    messages = [
        SystemMessage(system_prompt),
        UserMessage(policy_chunk)
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

def main():
    """
    Main function implementing the policy analysis workflow
    """
    # Load SOW and compliance guidelines
    sow_content = load_document("sample_data/sow.txt")
    policy_content = load_document("sample_data/compliance_guidelines.txt")
    
    if not sow_content or not policy_content:
        print("Failed to load required documents. Exiting.")
        return
    
    # Count tokens in SOW
    sow_tokens = count_tokens(sow_content)
    print(f"SOW contains {sow_tokens} tokens")
    
    # Calculate available tokens for policy content
    available_tokens = MAX_TOKENS - sow_tokens 
    print(f"Available tokens for policy content: {available_tokens}")
    
    if available_tokens <= 0:
        print("SOW is too large to process with the current MAX_TOKENS setting.")
        return
    
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
    
    # Generate final report
    print("\n--- FINAL REPORT ---")
    if all_violations:
        print("Policy violations found:")
        for i, violation in enumerate(all_violations):
            print(f"\nViolation Set {i+1}:\n{violation}")
    else:
        print("No policy violations were found.")
    
    # Optionally save report to file
    with open("policy_analysis_report.txt", "w", encoding="utf-8") as f:
        f.write("POLICY ANALYSIS REPORT\n")
        f.write("=====================\n\n")
        if all_violations:
            f.write("Policy violations found:\n")
            for i, violation in enumerate(all_violations):
                f.write(f"\nViolation Set {i+1}:\n{violation}\n")
        else:
            f.write("No policy violations were found.")
    
    print("\nReport saved to policy_analysis_report.txt")

if __name__ == "__main__":
    main()