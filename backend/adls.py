#adls.py
"""
Connect to Azure Data Lake Storage Gen2 using DefaultAzureCredential
and perform basic operations like listing containers, uploading, downloading files, and creating directories.
"""

import os
from azure.storage.filedatalake import DataLakeServiceClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import AzureError

# ============================================================================
# CONFIGURATION - UPDATE THESE VALUES
# ============================================================================
STORAGE_ACCOUNT_NAME = "djg0storage0shared"  # Replace with your storage account name
CONTAINER_NAME = "shared"              # Replace with your container/filesystem name
# ============================================================================

def connect_to_adls():
    """Connect to ADLS Gen2 using DefaultAzureCredential"""
    
    # Create the account URL using global config
    account_url = f"https://{STORAGE_ACCOUNT_NAME}.dfs.core.windows.net/"
    
    try:
        # Create credential using DefaultAzureCredential
        # This will automatically use az login credentials
        credential = DefaultAzureCredential()
        
        # Create the DataLakeServiceClient
        service_client = DataLakeServiceClient(
            account_url=account_url,
            credential=credential
        )
        
        print(f"✅ Successfully connected to {STORAGE_ACCOUNT_NAME}")
        return service_client
        
    except AzureError as e:
        print(f"❌ Azure error: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return None

def list_containers(service_client):
    """List all containers/file systems in the storage account"""
    try:
        print("\n📁 Available containers/file systems:")
        file_systems = service_client.list_file_systems()
        
        for fs in file_systems:
            print(f"  - {fs.name}")
            
    except AzureError as e:
        print(f"❌ Error listing containers: {e}")

def work_with_container(service_client, container_name):
    """Work with a specific container"""
    try:
        # Get file system client (container)
        file_system_client = service_client.get_file_system_client(container_name)
        
        # Check if container exists
        if file_system_client.exists():
            print(f"✅ Container '{container_name}' exists")
        else:
            print(f"⚠️ Container '{container_name}' does not exist")
            # Optionally create it
            # file_system_client.create_file_system()
            # print(f"✅ Created container '{container_name}'")
            return None
            
        return file_system_client
        
    except AzureError as e:
        print(f"❌ Error working with container: {e}")
        return None

def upload_file_example(file_system_client, local_file_path, remote_file_path):
    """Upload a file to ADLS Gen2"""
    try:
        # Get file client
        file_client = file_system_client.get_file_client(remote_file_path)
        
        # Read local file and upload
        with open(local_file_path, 'rb') as local_file:
            file_data = local_file.read()
            
            # Create file and upload data
            file_client.create_file()
            file_client.append_data(file_data, offset=0, length=len(file_data))
            file_client.flush_data(len(file_data))
            
        print(f"✅ Uploaded '{local_file_path}' to '{remote_file_path}'")
        
    except FileNotFoundError:
        print(f"❌ Local file '{local_file_path}' not found")
    except AzureError as e:
        print(f"❌ Error uploading file: {e}")

def download_file_example(file_system_client, remote_file_path, local_file_path):
    """Download a file from ADLS Gen2"""
    try:
        # Get file client
        file_client = file_system_client.get_file_client(remote_file_path)
        
        # Download file
        with open(local_file_path, 'wb') as local_file:
            download = file_client.download_file()
            download.readinto(local_file)
            
        print(f"✅ Downloaded '{remote_file_path}' to '{local_file_path}'")
        
    except AzureError as e:
        print(f"❌ Error downloading file: {e}")

def list_files_in_directory(file_system_client, directory_path=""):
    """List files and directories in a path"""
    try:
        print(f"\n📂 Contents of '{directory_path or 'root'}':")
        
        paths = file_system_client.get_paths(path=directory_path, recursive=False)
        
        for path in paths:
            path_type = "📁 DIR " if path.is_directory else "📄 FILE"
            print(f"  {path_type} {path.name}")
            
    except AzureError as e:
        print(f"❌ Error listing files: {e}")

def create_directory_example(file_system_client, directory_path):
    """Create a directory in ADLS Gen2"""
    try:
        directory_client = file_system_client.get_directory_client(directory_path)
        directory_client.create_directory()
        print(f"✅ Created directory '{directory_path}'")
        
    except AzureError as e:
        print(f"❌ Error creating directory: {e}")

def main():
    """Main function to demonstrate ADLS Gen2 operations"""
    
    # Validate configuration
    if STORAGE_ACCOUNT_NAME == "your-storage-account-name" or CONTAINER_NAME == "your-container-name":
        print("❌ Please update STORAGE_ACCOUNT_NAME and CONTAINER_NAME at the top of this file!")
        return
    
    print("🚀 Starting ADLS Gen2 Demo with DefaultAzureCredential")
    print("📋 Make sure you've run 'az login' first!")
    print(f"📁 Storage Account: {STORAGE_ACCOUNT_NAME}")
    print(f"📦 Container: {CONTAINER_NAME}")
    
    # Connect to ADLS
    service_client = connect_to_adls()
    if not service_client:
        print("❌ Failed to connect. Check your az login status and permissions.")
        return
    
    # List containers
    list_containers(service_client)
    
    # Work with specific container
    file_system_client = work_with_container(service_client, CONTAINER_NAME)
    if not file_system_client:
        print("❌ Cannot proceed without a valid container.")
        return
    
    # List files in root directory
    list_files_in_directory(file_system_client)
    
    # Example operations (uncomment to use):
    
    # Create a directory
    # create_directory_example(file_system_client, "test-directory")
    
    # Upload a file (make sure the local file exists)
    # upload_file_example(file_system_client, "local-file.txt", "remote-file.txt")
    
    # Download a file
    # download_file_example(file_system_client, "remote-file.txt", "downloaded-file.txt")
    
    # List files in a specific directory
    # list_files_in_directory(file_system_client, "test-directory")
    
    print("\n✅ Demo completed successfully!")

if __name__ == "__main__":
    main()