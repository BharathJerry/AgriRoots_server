import os
import uuid
import json
import requests
from fastapi import FastAPI, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import uvicorn
from PIL import Image
import torchvision.transforms.functional as TF
import CNN
import numpy as np
import torch
import pandas as pd
import imghdr
from datetime import datetime
from typing import Optional, List, Dict, Any

# Initialize FastAPI app
app = FastAPI(
    title="AgriRoots Plant Disease Detection API",
    description="AI-powered plant disease detection with location-based broadcast notifications",
    version="2.1.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
FCM_SERVER_URL = os.getenv("FCM_SERVER_URL", "http://10.114.135.36:5000")  # Your Flask FCM server
UPLOAD_DIR = "static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Load datasets
try:
    disease_info = pd.read_csv('disease_info.csv', encoding='cp1252')
    supplement_info = pd.read_csv('supplement_info.csv', encoding='cp1252')
    print("✅ Datasets loaded successfully")
    print(f"📊 Disease classes: {len(disease_info)}")
    print(f"📊 Supplement items: {len(supplement_info)}")
except Exception as e:
    print(f"❌ Error loading datasets: {e}")
    raise

# Load model
try:
    model = CNN.CNN(39)    
    model.load_state_dict(torch.load("plant_disease_model_1_latest.pt", map_location=torch.device('cpu')))
    model.eval()
    print("✅ AI Model loaded successfully")
except Exception as e:
    print(f"❌ Error loading model: {e}")
    raise

# Allowed image extensions
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

# Disease severity mapping based on your dataset
def get_disease_severity(disease_name: str) -> str:
    """Determine disease severity based on disease name"""
    disease_lower = disease_name.lower()
    
    # Critical diseases (most severe)
    if any(keyword in disease_lower for keyword in ['late blight', 'mosaic', 'yellow', 'curl', 'virus']):
        return 'critical'
    # High severity diseases
    elif any(keyword in disease_lower for keyword in ['early blight', 'bacterial', 'rust', 'mildew', 'spot']):
        return 'high'
    # Medium severity
    elif any(keyword in disease_lower for keyword in ['leaf', 'fungal', 'scab']):
        return 'medium'
    # Healthy or minor issues
    else:
        return 'low'

def get_image_format(file_path):
    """Detect image format"""
    image_type = imghdr.what(file_path)
    if image_type:
        return image_type
    ext = os.path.splitext(file_path)[1].lower().replace('.', '')
    if ext in ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']:
        return ext
    return None

def prediction(image_path):
    """Predict plant disease from image"""
    try:
        image = Image.open(image_path)
        image = image.resize((224, 224))
        input_data = TF.to_tensor(image)
        input_data = input_data.view((-1, 3, 224, 224))
        
        with torch.no_grad():
            output = model(input_data)
        
        output = output.detach().numpy()
        index = np.argmax(output)
        confidence = float(np.max(output))
        
        return index, confidence
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction error: {str(e)}")

async def send_location_broadcast_background(
    user_id: str,
    disease_name: str,
    severity: str,
    confidence: float,
    treatment: str,
    prevention: str = "Check app for prevention steps"
):
    """
    Send notification to ALL users in the same location as the user who uploaded the image
    """
    try:
        print(f"📍 Getting location for user {user_id}...")
        
        # First, get the user's location from the FCM server
        user_response = requests.get(
            f"{FCM_SERVER_URL}/api/debug/user/{user_id}",
            timeout=5
        )
        
        location = None
        username = "Unknown"
        
        if user_response.status_code == 200:
            user_data = user_response.json()
            location = user_data.get('location')
            username = user_data.get('username', 'Unknown')
            print(f"📍 User {username} location: {location}")
        else:
            print(f"⚠️ Could not fetch user info: {user_response.status_code}")
        
        if location and location != 'unknown' and location != '':
            # Broadcast to ALL users in the same location
            print(f"📤 Broadcasting disease alert to ALL users in {location}")
            
            broadcast_data = {
                "location": location,
                "disease": disease_name,
                "severity": severity,
                "confidence": confidence,
                "treatment": treatment,
                "prevention": prevention
            }
            
            # Send to location endpoint (this will notify ALL users in that location)
            response = requests.post(
                f"{FCM_SERVER_URL}/api/location-alert",
                json=broadcast_data,
                headers={"Content-Type": "application/json"},
                timeout=5
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Broadcast sent to {result.get('total_users', 0)} users in {location}")
                print(f"   Successful: {result.get('total_success', 0)}, Failed: {result.get('total_failure', 0)}")
                
                # Log the users who received it
                if 'successful_users' in result and result['successful_users']:
                    print(f"   Users notified: {[u['username'] for u in result['successful_users']]}")
            else:
                print(f"⚠️ Broadcast error: {response.status_code} - {response.text}")
        else:
            # Fallback: send only to the user if location not found
            print(f"⚠️ No location found for user {user_id}, sending only to them")
            fcm_data = {
                "userId": user_id,
                "disease": disease_name,
                "severity": severity,
                "confidence": confidence,
                "treatment": treatment,
                "prevention": prevention
            }
            
            response = requests.post(
                f"{FCM_SERVER_URL}/api/disease-alert",
                json=fcm_data,
                headers={"Content-Type": "application/json"},
                timeout=5
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Notification sent to user {user_id}")
            else:
                print(f"⚠️ Failed to send notification: {response.status_code}")
            
    except requests.exceptions.ConnectionError:
        print(f"❌ Could not connect to FCM server at {FCM_SERVER_URL}")
    except Exception as e:
        print(f"❌ Error sending notification: {e}")

@app.get("/")
async def root():
    return {
        "message": "AgriRoots Plant Disease Detection API", 
        "status": "active",
        "version": "2.1.0",
        "fcm_server": FCM_SERVER_URL,
        "endpoints": {
            "health": "/health",
            "predict": "/predict/{user_id} (for logged-in users) - BROADCASTS TO LOCATION",
            "predict_anonymous": "/predict (for guests)",
            "diseases": "/diseases",
            "marketplace": "/market",
            "broadcast_all": "/broadcast-disease (POST) - Send to ALL users",
            "broadcast_location": "/broadcast-by-location/{location} (POST) - Send to users in specific location",
            "broadcast_multiple": "/broadcast-multiple-locations (POST) - Send to multiple locations",
            "user_stats": "/user-stats (GET)",
            "location_users": "/location-users/{location} (GET) - Check users in location",
            "user_info": "/user-info/{user_id} (GET) - Get user information",
            "test_notification": "/test-notification/{user_id} (POST)"
        }
    }

@app.get("/health")
async def health_check():
    # Check if FCM server is reachable
    fcm_status = "unknown"
    fcm_details = {}
    fcm_error = None
    
    try:
        response = requests.get(f"{FCM_SERVER_URL}/health", timeout=2)
        if response.status_code == 200:
            fcm_status = "connected"
            fcm_details = response.json()
        else:
            fcm_status = "error"
            fcm_error = f"Status code: {response.status_code}"
    except requests.exceptions.ConnectionError:
        fcm_status = "disconnected"
        fcm_error = "Connection refused"
    except Exception as e:
        fcm_status = "error"
        fcm_error = str(e)
    
    return {
        "status": "healthy", 
        "model_loaded": True,
        "fcm_server": {
            "url": FCM_SERVER_URL,
            "status": fcm_status,
            "details": fcm_details,
            "error": fcm_error
        },
        "timestamp": datetime.now().isoformat()
    }

@app.post("/predict/{user_id}")
async def predict_disease_for_user(
    user_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    """
    Predict plant disease and send notification to ALL users in the same location
    """
    print(f"📨 Received file for user {user_id}: {file.filename}")
    
    file_path = None
    try:
        # Validate file
        file_extension = os.path.splitext(file.filename)[1].lower()
        if file_extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400, 
                detail=f"File extension not allowed. Allowed: {ALLOWED_EXTENSIONS}"
            )
        
        # Save file
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        file_path = os.path.join(UPLOAD_DIR, unique_filename)
        
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)
        
        print(f"📸 Image saved: {unique_filename} (Size: {len(content)} bytes)")
        
        # Validate image
        image_format = get_image_format(file_path)
        if not image_format:
            os.remove(file_path)
            raise HTTPException(status_code=400, detail="Invalid image file")
        
        print(f"✅ Image format detected: {image_format}")
        
        # Make prediction
        pred_index, confidence = prediction(file_path)
        print(f"🔍 Prediction index: {pred_index}, Confidence: {confidence}")
        
        # Get disease information
        disease_name = str(disease_info['disease_name'][pred_index])
        description = str(disease_info['description'][pred_index])
        prevention = str(disease_info['Possible Steps'][pred_index])
        disease_image = str(disease_info['image_url'][pred_index])
        
        # Get supplement information
        supplement_name = str(supplement_info['supplement name'][pred_index])
        supplement_image = str(supplement_info['supplement image'][pred_index])
        supplement_buy_link = str(supplement_info['buy link'][pred_index])
        
        # Determine severity
        severity = get_disease_severity(disease_name)
        
        print(f"🎯 Prediction: {disease_name}")
        print(f"⚠️ Severity: {severity}")
        print(f"📊 Confidence: {confidence:.4f}")
        
        # Send location-based broadcast notification in background
        notification_sent = False
        if user_id != 'anonymous' and user_id != 'test':
            background_tasks.add_task(
                send_location_broadcast_background,
                user_id=user_id,
                disease_name=disease_name,
                severity=severity,
                confidence=confidence,
                treatment=supplement_name,
                prevention=prevention
            )
            notification_sent = True
            print(f"📱 Location broadcast notification queued for all users in {user_id}'s location")
        else:
            print(f"⚠️ Anonymous user - no notification sent")
        
        # Prepare response
        response_data = {
            "success": True,
            "user_id": user_id,
            "notification_sent": notification_sent,
            "notification_type": "location_broadcast" if notification_sent else "none",
            "prediction": {
                "disease_id": int(pred_index),
                "disease_name": disease_name,
                "confidence": round(confidence, 4),
                "severity": severity,
                "description": description,
                "prevention_steps": prevention,
                "reference_image": disease_image
            },
            "treatment": {
                "supplement_name": supplement_name,
                "supplement_image": supplement_image,
                "buy_link": supplement_buy_link
            },
            "uploaded_image_url": f"/static/uploads/{unique_filename}",
            "timestamp": datetime.now().isoformat()
        }
        
        return JSONResponse(content=response_data)
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error: {e}")
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")

@app.post("/predict")
async def predict_disease_anonymous(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    """Predict disease without notification (for anonymous users)"""
    return await predict_disease_for_user("anonymous", background_tasks, file)

@app.get("/diseases")
async def get_all_diseases():
    """Get all diseases information"""
    diseases = []
    for idx in range(len(disease_info)):
        disease_name = str(disease_info['disease_name'][idx])
        diseases.append({
            "id": idx,
            "name": disease_name,
            "severity": get_disease_severity(disease_name),
            "description": str(disease_info['description'][idx]),
            "prevention": str(disease_info['Possible Steps'][idx]),
            "image_url": str(disease_info['image_url'][idx])
        })
    
    return {"diseases": diseases, "count": len(diseases)}

@app.get("/market")
async def get_marketplace():
    """Get marketplace data"""
    supplements = []
    for idx in range(len(supplement_info)):
        supplements.append({
            "id": idx,
            "name": str(supplement_info['supplement name'][idx]),
            "image": str(supplement_info['supplement image'][idx]),
            "buy_link": str(supplement_info['buy link'][idx]),
            "for_disease": str(disease_info['disease_name'][idx])
        })
    
    return {"supplements": supplements, "count": len(supplements)}

# Endpoint to broadcast disease alerts to ALL users
@app.post("/broadcast-disease")
async def broadcast_disease_alert(
    disease_data: Dict[str, Any]
):
    """
    Broadcast disease alert to ALL users
    Expected JSON:
    {
        "disease": "late_blight",
        "severity": "critical",
        "confidence": 0.95,
        "treatment": "Remove infected plants",
        "prevention": "Use resistant varieties"
    }
    """
    try:
        # Validate required fields
        required_fields = ['disease', 'severity']
        for field in required_fields:
            if field not in disease_data:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        
        print(f"📤 Broadcasting disease alert to ALL users: {disease_data['disease']}")
        
        # Send broadcast via FCM server
        response = requests.post(
            f"{FCM_SERVER_URL}/api/broadcast-disease",
            json=disease_data,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            return {
                "success": True,
                "message": f"Disease alert broadcast to {result.get('total_users', 0)} users",
                "data": result
            }
        else:
            error_detail = response.text
            try:
                error_detail = response.json()
            except:
                pass
            raise HTTPException(status_code=response.status_code, detail=error_detail)
            
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="FCM server unavailable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to broadcast disease alerts to users in a specific location
@app.post("/broadcast-by-location/{location}")
async def broadcast_disease_by_location(
    location: str,
    disease_data: Dict[str, Any]
):
    """
    Broadcast disease alert to users in a specific location
    Expected JSON:
    {
        "disease": "late_blight",
        "severity": "critical",
        "confidence": 0.95,
        "treatment": "Remove infected plants",
        "prevention": "Use resistant varieties"
    }
    """
    try:
        # Validate required fields
        required_fields = ['disease', 'severity']
        for field in required_fields:
            if field not in disease_data:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        
        print(f"📤 Broadcasting disease alert to users in {location}: {disease_data['disease']}")
        
        # Prepare data with location
        payload = {
            "location": location,
            **disease_data
        }
        
        # Send location-based alert via FCM server
        response = requests.post(
            f"{FCM_SERVER_URL}/api/location-alert",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            return {
                "success": True,
                "message": f"Disease alert broadcast to {result.get('total_users', 0)} users in {location}",
                "data": result
            }
        else:
            error_detail = response.text
            try:
                error_detail = response.json()
            except:
                pass
            raise HTTPException(status_code=response.status_code, detail=error_detail)
            
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="FCM server unavailable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to broadcast to multiple locations
@app.post("/broadcast-multiple-locations")
async def broadcast_to_multiple_locations(
    data: Dict[str, Any]
):
    """
    Broadcast disease alert to multiple locations
    Expected JSON:
    {
        "locations": ["Salem", "Coimbatore", "Erode"],
        "disease": "late_blight",
        "severity": "critical",
        "confidence": 0.95,
        "treatment": "Remove infected plants",
        "prevention": "Use resistant varieties"
    }
    """
    try:
        # Validate required fields
        required_fields = ['locations', 'disease', 'severity']
        for field in required_fields:
            if field not in data:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        
        locations = data['locations']
        if not isinstance(locations, list) or len(locations) == 0:
            raise HTTPException(status_code=400, detail="Locations must be a non-empty list")
        
        print(f"📤 Broadcasting disease alert to {len(locations)} locations")
        
        # Prepare disease data
        disease_data = {
            'disease': data['disease'],
            'severity': data['severity'],
            'confidence': data.get('confidence', 0.0),
            'treatment': data.get('treatment', ''),
            'prevention': data.get('prevention', '')
        }
        
        results = []
        total_users = 0
        failed_locations = []
        
        # Send to each location
        for location in locations:
            payload = {
                "location": location,
                **disease_data
            }
            
            try:
                response = requests.post(
                    f"{FCM_SERVER_URL}/api/location-alert",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=10
                )
                
                if response.status_code == 200:
                    result = response.json()
                    total_users += result.get('total_users', 0)
                    results.append({
                        "location": location,
                        "success": True,
                        "users": result.get('total_users', 0),
                        "successful": result.get('total_success', 0),
                        "failed": result.get('total_failure', 0)
                    })
                else:
                    failed_locations.append(location)
                    results.append({
                        "location": location,
                        "success": False,
                        "error": f"HTTP {response.status_code}"
                    })
            except Exception as e:
                failed_locations.append(location)
                results.append({
                    "location": location,
                    "success": False,
                    "error": str(e)
                })
        
        return {
            "success": len(failed_locations) == 0,
            "total_locations": len(locations),
            "total_users": total_users,
            "failed_locations": failed_locations,
            "results": results
        }
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to get user statistics
@app.get("/user-stats")
async def get_user_statistics():
    """Get statistics about users from FCM server"""
    try:
        response = requests.get(f"{FCM_SERVER_URL}/api/user-stats", timeout=5)
        
        if response.status_code == 200:
            return response.json()
        else:
            return {
                "success": False,
                "error": "Could not fetch user stats",
                "status_code": response.status_code
            }
            
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": "FCM server unavailable"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

# Endpoint to send test notification
@app.post("/test-notification/{user_id}")
async def send_test_notification(user_id: str):
    """Send a test notification to a specific user"""
    try:
        test_data = {
            "userId": user_id,
            "disease": "Test Disease",
            "severity": "low",
            "confidence": 0.5,
            "treatment": "This is a test notification",
            "prevention": "Testing your notification system"
        }
        
        response = requests.post(
            f"{FCM_SERVER_URL}/api/disease-alert",
            json=test_data,
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        
        if response.status_code == 200:
            return {
                "success": True,
                "message": f"Test notification sent to user {user_id}",
                "data": response.json()
            }
        else:
            return {
                "success": False,
                "error": f"FCM server error: {response.status_code}"
            }
            
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": "FCM server unavailable"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

# Endpoint to get user info from Firestore (proxy)
@app.get("/user-info/{user_id}")
async def get_user_info(user_id: str):
    """Get user information from Firestore via FCM server"""
    try:
        response = requests.get(
            f"{FCM_SERVER_URL}/api/debug/user/{user_id}",
            timeout=5
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            return {
                "success": False,
                "error": f"Could not fetch user info: {response.status_code}"
            }
            
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": "FCM server unavailable"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

# Endpoint to check if a location has users
@app.get("/location-users/{location}")
async def get_users_in_location(location: str):
    """Get count of users in a specific location"""
    try:
        # First get user stats
        stats_response = requests.get(f"{FCM_SERVER_URL}/api/user-stats", timeout=5)
        
        if stats_response.status_code == 200:
            stats = stats_response.json()
            location_distribution = stats.get('location_distribution', {})
            
            # Check if location exists
            user_count = location_distribution.get(location, 0)
            
            return {
                "success": True,
                "location": location,
                "user_count": user_count,
                "has_users": user_count > 0
            }
        else:
            return {
                "success": False,
                "error": "Could not fetch user stats"
            }
            
    except requests.exceptions.ConnectionError:
        return {
            "success": False,
            "error": "FCM server unavailable"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

# Endpoint to get disease by ID
@app.get("/disease/{disease_id}")
async def get_disease_by_id(disease_id: int):
    """Get disease information by ID"""
    try:
        if disease_id < 0 or disease_id >= len(disease_info):
            raise HTTPException(status_code=404, detail="Disease not found")
        
        disease_name = str(disease_info['disease_name'][disease_id])
        return {
            "id": disease_id,
            "name": disease_name,
            "severity": get_disease_severity(disease_name),
            "description": str(disease_info['description'][disease_id]),
            "prevention": str(disease_info['Possible Steps'][disease_id]),
            "image_url": str(disease_info['image_url'][disease_id])
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint to check server connectivity
@app.get("/ping")
async def ping():
    """Simple ping endpoint to check if server is alive"""
    return {"ping": "pong", "timestamp": datetime.now().isoformat()}

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    print("🚀 Starting AgriRoots Disease Detection API...")
    print("=" * 70)
    print(f"📡 FCM Server: {FCM_SERVER_URL}")
    print(f"📁 Upload directory: {UPLOAD_DIR}")
    print(f"📚 API docs: http://localhost:8000/docs")
    print(f"🔍 Health check: http://localhost:8000/health")
    print(f"🏓 Ping test: http://localhost:8000/ping")
    print("\n📋 Available endpoints:")
    print("   POST /predict/{user_id}          - Analyze image for logged-in user")
    print("                                      🔔 BROADCASTS to ALL users in same location")
    print("   POST /predict                     - Analyze image for anonymous user")
    print("   GET  /diseases                    - List all diseases")
    print("   GET  /disease/{id}                 - Get disease by ID")
    print("   GET  /market                       - List all supplements")
    print("   POST /broadcast-disease            - Broadcast alert to ALL users")
    print("   POST /broadcast-by-location/{loc}  - Broadcast alert to users in a location")
    print("   POST /broadcast-multiple-locations - Broadcast to multiple locations")
    print("   GET  /user-stats                   - Get user statistics")
    print("   GET  /location-users/{location}    - Check users in a location")
    print("   GET  /user-info/{user_id}          - Get user information")
    print("   POST /test-notification/{id}       - Send test notification")
    print("   GET  /ping                         - Simple connectivity test")
    print("=" * 70)
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=True
    )