"""
Azure AI Foundry GPT-4.1 Base64 Image Demo - Synchronous Version
Using the correct get_openai_client() pattern from your FastAPI app
"""

import os
import base64
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

# Your environment variables
PROJECT_ENDPOINT = "https://djg-ai-foundry.services.ai.azure.com/api/projects/shared_project"
MODEL_DEPLOYMENT_NAME = "gpt-4.1"
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT = "true"

class Base64ImageDemo:
    def __init__(self):
        """Initialize the Azure AI Foundry client using your pattern"""
        self.project_client = AIProjectClient(
            credential=DefaultAzureCredential(),
            endpoint=PROJECT_ENDPOINT,
        )
        
        # Get OpenAI client using your exact pattern
        self.openai_client = self.project_client.get_openai_client(
            api_version="2024-02-01"
        )
        print(f"✅ Connected to Azure AI Foundry project")
    
    def encode_image_file(self, image_path: str) -> str:
        """
        Convert image file to base64 string
        
        Args:
            image_path: Path to the local image file
            
        Returns:
            Base64 encoded string (raw, without data: prefix)
        """
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    
    def analyze_with_base64(self, base64_string: str, mime_type: str = "image/jpeg", prompt: str = "Describe this image"):
        """
        Analyze image using base64 string (synchronous)
        
        Args:
            base64_string: Raw base64 encoded image data
            mime_type: MIME type of the image
            prompt: Text prompt to accompany the image
        """
        try:
            # Format as proper data URL for the API
            data_url = f"data:{mime_type};base64,{base64_string}"
            
            messages = [
                {"role": "system", "content": "You are a helpful assistant that analyzes images."},
                {"role": "user", "content": [
                    {
                        "type": "text",
                        "text": prompt
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url,
                            "detail": "high"
                        }
                    }
                ]}
            ]
            
            # Use the same pattern as your FastAPI app
            response = self.openai_client.chat.completions.create(
                model=MODEL_DEPLOYMENT_NAME,
                messages=messages,
                max_tokens=1500,
                temperature=0.7
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            print(f"❌ Error analyzing image: {str(e)}")
            return None
    
    def analyze_image_file(self, image_path: str, prompt: str = "What do you see in this image?"):
        """
        Load image file and analyze using base64 (synchronous)
        
        Args:
            image_path: Path to the local image file
            prompt: Text prompt to accompany the image
        """
        try:
            # Read and encode the image
            base64_string = self.encode_image_file(image_path)
            print(f"📁 Encoded image from: {image_path}")
            print(f"📊 Base64 length: {len(base64_string)} characters")
            
            # Determine MIME type from file extension
            if image_path.lower().endswith('.png'):
                mime_type = "image/png"
            elif image_path.lower().endswith(('.jpg', '.jpeg')):
                mime_type = "image/jpeg"
            elif image_path.lower().endswith('.webp'):
                mime_type = "image/webp"
            elif image_path.lower().endswith('.gif'):
                mime_type = "image/gif"
            else:
                mime_type = "image/jpeg"  # Default fallback
            
            return self.analyze_with_base64(base64_string, mime_type, prompt)
            
        except Exception as e:
            print(f"❌ Error processing file: {str(e)}")
            return None
    
    def analyze_multiple_images(self, base64_images: list, prompt: str = "Analyze these images"):
        """
        Analyze multiple base64 images in a single request (synchronous)
        
        Args:
            base64_images: List of dictionaries with 'data' and 'mime_type' keys
            prompt: Text prompt to accompany the images
        """
        try:
            # Build the content array
            content = [{"type": "text", "text": prompt}]
            
            for i, img_data in enumerate(base64_images):
                data_url = f"data:{img_data['mime_type']};base64,{img_data['data']}"
                
                image_content = {
                    "type": "image_url",
                    "image_url": {
                        "url": data_url,
                        "detail": "high"
                    }
                }
                
                content.append(image_content)
                print(f"📸 Added base64 image {i+1} ({img_data['mime_type']})")
            
            messages = [
                {"role": "system", "content": "You are a helpful assistant that can analyze and compare images."},
                {"role": "user", "content": content}
            ]
            
            # Use the same pattern as your FastAPI app
            response = self.openai_client.chat.completions.create(
                model=MODEL_DEPLOYMENT_NAME,
                messages=messages,
                max_tokens=2000,
                temperature=0.7
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            print(f"❌ Error in batch analysis: {str(e)}")
            return None

def main():
    """Main demonstration function (synchronous)"""
    print("🚀 Azure AI Foundry GPT-4.1 Base64 Image Demo (Sync Version)\n")
    
    # Initialize the demo class
    demo = Base64ImageDemo()
    
    # Example 1: Using raw base64 string directly
    print("=" * 60)
    print("Example 1: Raw Base64 String Analysis")
    print("=" * 60)
    
    # Sample base64 string (tiny 1x1 red pixel PNG for demo)
    sample_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
    
    result = demo.analyze_with_base64(
        base64_string=sample_base64,
        mime_type="image/png",
        prompt="Describe what you see in this image, including colors and dimensions."
    )
    
    if result:
        print(f"🔍 Analysis Result:\n{result}\n")
    
    # Example 2: Analyze local image file
    print("=" * 60)
    print("Example 2: Local File Analysis")
    print("=" * 60)
    
    # Replace with your actual image path
    local_image_path = "path/to/your/image.jpg"
    
    if os.path.exists(local_image_path):
        result = demo.analyze_image_file(
            image_path=local_image_path,
            prompt="Provide a detailed analysis of this image including any text, objects, and overall composition."
        )
        
        if result:
            print(f"🔍 File Analysis:\n{result}\n")
    else:
        print(f"⚠️  Local image not found at: {local_image_path}")
        print("📝 To test with a real image, update the 'local_image_path' variable")
    
    # Example 3: Multiple images at once
    print("=" * 60)
    print("Example 3: Multiple Base64 Images")
    print("=" * 60)
    
    # Example with multiple base64 strings
    sample_images = [
        {
            "data": sample_base64,
            "mime_type": "image/png"
        }
        # Add more images here as needed
    ]
    
    result = demo.analyze_multiple_images(
        base64_images=sample_images,
        prompt="Analyze these images and describe what you see in each one."
    )
    
    if result:
        print(f"🔄 Multi-image Analysis:\n{result}\n")
    
    print("✅ Demo completed successfully!")

# Helper functions matching your style
def create_base64_from_file(image_path: str) -> dict:
    """
    Helper function to create base64 data from an image file
    Matches the pattern used in your FastAPI app
    
    Args:
        image_path: Path to the image file
        
    Returns:
        Dictionary with 'data' and 'mime_type' keys
    """
    try:
        # Determine MIME type (matching your mimetypes approach)
        import mimetypes
        content_type = mimetypes.guess_type(image_path)[0]
        
        if content_type and content_type.startswith('image/'):
            mime_type = content_type
        elif image_path.lower().endswith('.png'):
            mime_type = "image/png"
        elif image_path.lower().endswith(('.jpg', '.jpeg')):
            mime_type = "image/jpeg"
        elif image_path.lower().endswith('.webp'):
            mime_type = "image/webp"
        elif image_path.lower().endswith('.gif'):
            mime_type = "image/gif"
        else:
            mime_type = "image/jpeg"  # Default fallback
        
        # Read and encode
        with open(image_path, "rb") as image_file:
            base64_data = base64.b64encode(image_file.read()).decode('utf-8')
        
        return {
            "data": base64_data,
            "mime_type": mime_type
        }
        
    except Exception as e:
        print(f"❌ Error creating base64: {str(e)}")
        return None

if __name__ == "__main__":
    # Run the synchronous demo
    main()
    
    # Example usage matching your FastAPI patterns
    print("\n" + "=" * 60)
    print("Integration Example: How to add this to your FastAPI app")
    print("=" * 60)
    
    print("""
# Add this to your FastAPI app imports:
from your_base64_image_module import Base64ImageDemo

# Add this endpoint to your FastAPI app:
@app.post("/analyze-image")
async def analyze_image_endpoint(
    image_base64: str = Form(...),
    mime_type: str = Form("image/jpeg"),
    prompt: str = Form("Describe this image")
):
    try:
        demo = Base64ImageDemo()
        result = demo.analyze_with_base64(image_base64, mime_type, prompt)
        
        if result:
            return {"success": True, "analysis": result}
        else:
            return {"success": False, "message": "Analysis failed"}
            
    except Exception as e:
        return {"success": False, "message": str(e)}
    """)
