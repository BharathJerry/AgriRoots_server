import os
import json
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, messaging, firestore
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Firebase initialization
cred_path = os.getenv("FIREBASE_CREDENTIALS")
project_id = os.getenv("FIREBASE_PROJECT_ID")
sender_id = os.getenv("FIREBASE_SENDER_ID", "70868825299")

if not cred_path:
    raise ValueError("Firebase credentials path not found in .env file")

if not os.path.exists(cred_path):
    raise FileNotFoundError(f"Credentials file not found at: {cred_path}")

try:
    # Initialize Firebase
    if not firebase_admin._apps:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred, {
            'projectId': project_id
        })
        logger.info("✅ Firebase initialized successfully")
        logger.info(f"📱 Sender ID: {sender_id}")
        logger.info(f"📁 Using credentials from: {cred_path}")
    
    # Initialize Firestore
    db = firestore.client()
    logger.info("✅ Firestore initialized successfully")
    
except Exception as e:
    logger.error(f"❌ Failed to initialize Firebase: {e}")
    raise


class FCMNotificationService:
    """Service for handling FCM notifications (v1 API)"""
    
    def __init__(self):
        self.db = firestore.client()
        self.sender_id = os.getenv("FIREBASE_SENDER_ID", "70868825299")
    
    def validate_token(self, token: str) -> bool:
        """
        Validate if an FCM token is valid
        """
        if not token or len(token) < 50:
            logger.warning(f"❌ Invalid token length: {len(token) if token else 0}")
            return False
        
        # Basic pattern validation for FCM tokens
        if ':' in token and len(token) > 100:  # Android token pattern
            logger.info(f"✅ Valid Android token format")
            return True
        elif len(token) > 60:  # iOS token pattern
            logger.info(f"✅ Valid iOS token format")
            return True
        else:
            logger.warning(f"❌ Token doesn't match expected pattern")
            return False
    
    def _get_user_token(self, user_data: Dict) -> Optional[str]:
        """Helper method to get token from either field name (fcmToken or fcToken)"""
        return user_data.get('fcmToken') or user_data.get('fcToken')

    def send_disease_alert(
        self,
        user_id: str,
        disease_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Send disease detection alert to a specific user
        """
        try:
            logger.info(f"🔍 Looking up user: {user_id}")
            
            # Get user's FCM token from Firestore
            user_ref = self.db.collection('users').document(user_id)
            user_doc = user_ref.get()
            
            if not user_doc.exists:
                logger.error(f"❌ User {user_id} not found in Firestore")
                return {
                    "status": "error",
                    "error": "User not found in Firestore"
                }
            
            user_data = user_doc.to_dict()
            fcm_token = self._get_user_token(user_data)
            
            if not fcm_token:
                logger.error(f"❌ No FCM token for user {user_id}")
                return {
                    "status": "error",
                    "error": "User has no FCM token"
                }
            
            # Extract disease information
            disease_name = disease_data.get('disease', 'unknown')
            severity = disease_data.get('severity', 'medium')
            confidence = disease_data.get('confidence', 0.0)
            confidence_percent = confidence * 100  # Convert to percentage
            
            # Format confidence to 1 decimal place
            confidence_formatted = f"{confidence_percent:.1f}%"
            
            # Create notification title with severity emoji
            if severity == 'critical':
                title = "🚨 CRITICAL: Disease Alert"
            elif severity == 'high':
                title = "⚠️ URGENT: Disease Alert"
            else:
                title = "🌱 Disease Alert"
            
            # Create notification body with all details
            body = f"{disease_name}\nSeverity: {severity.upper()}\nConfidence: {confidence_formatted}"
            
            logger.info(f"📱 Preparing notification: {title}")
            
            # Build message for FCM v1
            message = messaging.Message(
                notification=messaging.Notification(
                    title=title,
                    body=body,
                ),
                data={
                    'type': 'disease_alert',
                    'disease': disease_name,
                    'severity': severity,
                    'confidence': str(confidence),
                    'confidence_percent': confidence_formatted,
                    'timestamp': datetime.now().isoformat(),
                    'treatment': disease_data.get('treatment', ''),
                    'prevention': disease_data.get('prevention', ''),
                    'click_action': 'FLUTTER_NOTIFICATION_CLICK'
                },
                token=fcm_token,
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        channel_id='disease_alerts',
                        color='#FF0000' if severity == 'critical' else '#FFA500' if severity == 'high' else '#FFD700',
                        icon='ic_notification',
                        click_action='FLUTTER_NOTIFICATION_CLICK',
                        visibility='public',
                        sound='default',
                    )
                ),
                apns=messaging.APNSConfig(
                    headers={
                        "apns-priority": "10",
                    },
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            alert=messaging.ApsAlert(
                                title=title,
                                body=body
                            ),
                            sound='default',
                            badge=1,
                            content_available=True,
                        )
                    )
                )
            )
            
            # Send message
            logger.info(f"📤 Sending FCM message...")
            response = messaging.send(message)
            logger.info(f"✅ Disease alert sent to user {user_id}: {response}")
            
            # Save to Firestore for history
            self._save_notification_history(user_id, title, body, disease_data, response)
            
            return {
                "status": "success",
                "response": response,
                "message_id": response,
                "severity": severity,
                "token_preview": fcm_token[:20] + "..."
            }
            
        except messaging.UnregisteredError:
            logger.error(f"❌ Token invalid for user {user_id}")
            self._handle_invalid_token_by_user(user_id)
            return {
                "status": "error",
                "error": "Device token is no longer valid (unregistered)",
                "error_code": "unregistered"
            }
        except Exception as e:
            logger.error(f"❌ Error sending disease alert: {e}")
            return {
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__
            }
    
    def send_to_device(
        self, 
        token: str, 
        title: str, 
        body: str, 
        data: Optional[Dict] = None,
        android_channel_id: str = "disease_alerts"
    ) -> Dict[str, Any]:
        """
        Send notification to a specific device
        """
        try:
            # Validate token first
            if not self.validate_token(token):
                return {
                    "status": "error",
                    "error": "Invalid token format",
                    "token_preview": token[:20] + "..." if token else None
                }
            
            logger.info(f"📱 Sending to device: {token[:20]}...")
            
            # Build message for FCM v1
            message = messaging.Message(
                notification=messaging.Notification(
                    title=title,
                    body=body,
                ),
                android=messaging.AndroidConfig(
                    priority="high",  # Always high priority
                    notification=messaging.AndroidNotification(
                        channel_id=android_channel_id,
                        visibility='public',
                        click_action="FLUTTER_NOTIFICATION_CLICK",
                        sound="default"
                    ),
                ),
                apns=messaging.APNSConfig(
                    headers={"apns-priority": "10"},
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            alert=messaging.ApsAlert(
                                title=title,
                                body=body
                            ),
                            sound="default",
                            badge=1,
                            content_available=True,
                        ),
                    ),
                ),
                token=token,
                data=data or {}
            )
            
            # Send message
            response = messaging.send(message)
            logger.info(f"✅ Successfully sent message to device: {response}")
            
            # Log successful send to Firestore
            self._log_notification(token, title, body, data, "sent", response)
            
            return {
                "status": "success",
                "response": response,
                "message_id": response
            }
            
        except messaging.UnregisteredError:
            logger.error(f"❌ Token {token[:20]}... is unregistered")
            self._handle_invalid_token(token)
            return {
                "status": "error",
                "error": "Token is no longer valid (unregistered)",
                "error_code": "unregistered"
            }
        except Exception as e:
            logger.error(f"❌ Error sending message: {e}")
            return {
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__
            }
    
    def send_to_topic(
        self, 
        topic: str, 
        title: str, 
        body: str, 
        data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Send notification to a topic (e.g., all users in a region)
        """
        try:
            message = messaging.Message(
                notification=messaging.Notification(
                    title=title,
                    body=body,
                ),
                topic=topic,
                data=data or {}
            )
            
            response = messaging.send(message)
            logger.info(f"✅ Successfully sent message to topic '{topic}': {response}")
            
            return {
                "status": "success",
                "response": response,
                "topic": topic
            }
            
        except Exception as e:
            logger.error(f"❌ Error sending topic message: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def send_to_users_by_location(
        self,
        location: str,
        title: str,
        body: str,
        data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Send notification to all users in a specific location
        Includes reporting user information in the notification data
        """
        try:
            logger.info(f"📤 Sending notification to users in location: {location}")
            
            # Get all users with location
            users_ref = self.db.collection('users')
            
            # Get all users with the location (no composite query needed)
            location_users = users_ref.where('location', '==', location).get()
            
            # Get reporting user info from data (handle None case)
            reporting_user = {}
            if data and isinstance(data, dict):
                reporting_user = data.get('reporting_user', {})
                if reporting_user is None:
                    reporting_user = {}
            
            # Ensure reporting_user is a dict
            if not isinstance(reporting_user, dict):
                reporting_user = {}
            
            # Filter users with valid tokens in memory
            tokens = []
            user_ids = []
            user_names = []
            user_details = []
            successful_users = []
            failed_users = []
            
            for user in location_users:
                user_data = user.to_dict()
                token = self._get_user_token(user_data)
                username = user_data.get('username', 'Unknown')
                email = user_data.get('email', 'No email')
                
                logger.info(f"   📄 User: {username} (ID: {user.id}, Email: {email})")
                
                user_details.append({
                    'user_id': user.id,
                    'username': username,
                    'email': email,
                    'has_token': token is not None
                })
                
                if token and self.validate_token(token):
                    tokens.append(token)
                    user_ids.append(user.id)
                    user_names.append(username)
                    logger.info(f"      ✅ Valid token: {token[:20]}...")
                else:
                    logger.warning(f"      ❌ Invalid or missing token for user {username}")
            
            if not tokens:
                logger.warning(f"⚠️ No users in location '{location}' with valid tokens")
                return {
                    "status": "error",
                    "error": f"No users in {location} with valid tokens",
                    "users_found": len(location_users),
                    "users_without_tokens": len(location_users) - len(tokens),
                    "user_details": user_details
                }
            
            logger.info(f"✅ Found {len(tokens)} users with valid tokens in {location}")
            
            # Send individually
            total_success = 0
            total_failure = 0
            
            for i, (token, user_id, username) in enumerate(zip(tokens, user_ids, user_names)):
                try:
                    # Prepare notification data with reporting user info
                    notification_data = {
                        'type': 'location_alert',
                        'location': location,
                        'reported_by': reporting_user.get('username', 'Unknown Farmer'),
                        'reported_by_id': reporting_user.get('user_id', ''),
                        'disease': data.get('disease', 'Unknown') if data else 'Unknown',
                        'severity': data.get('severity', 'medium') if data else 'medium',
                        'confidence': str(data.get('confidence', 0.0)) if data else '0.0',
                        'confidence_percent': data.get('confidence_percent', '0%') if data else '0%',
                        'treatment': data.get('treatment', '') if data else '',
                        'prevention': data.get('prevention', '') if data else '',
                        'timestamp': datetime.now().isoformat(),
                        'click_action': 'FLUTTER_NOTIFICATION_CLICK'
                    }
                    
                    # Build message for each user
                    message = messaging.Message(
                        notification=messaging.Notification(
                            title=title,
                            body=body,
                        ),
                        data=notification_data,
                        token=token,
                        android=messaging.AndroidConfig(
                            priority="high",
                            notification=messaging.AndroidNotification(
                                channel_id='location_alerts',
                                visibility='public',
                                sound='default',
                                click_action='FLUTTER_NOTIFICATION_CLICK',
                            )
                        )
                    )
                    
                    response = messaging.send(message)
                    total_success += 1
                    logger.info(f"      ✅ Notification sent to {username}")
                    successful_users.append({
                        'user_id': user_id,
                        'username': username
                    })
                    
                except messaging.UnregisteredError:
                    logger.error(f"      ❌ Token invalid for user {username}")
                    total_failure += 1
                    failed_users.append({
                        'user_id': user_id,
                        'username': username,
                        'error': 'Token unregistered'
                    })
                    self._handle_invalid_token(token)
                except Exception as e:
                    logger.error(f"      ❌ Failed to send to {username}: {e}")
                    total_failure += 1
                    failed_users.append({
                        'user_id': user_id,
                        'username': username,
                        'error': str(e)
                    })
            
            return {
                "status": "success",
                "total_users": len(tokens),
                "location": location,
                "total_success": total_success,
                "total_failure": total_failure,
                "successful_users": successful_users,
                "failed_users": failed_users,
                "reporting_user": reporting_user.get('username', 'Unknown Farmer') if reporting_user else 'Unknown Farmer',
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ Error sending to location: {e}")
            return {
                "status": "error",
                "error": str(e)
            }

    def send_disease_alert_by_location(
        self,
        location: str,
        disease_data: Dict[str, Any],
        reporting_user: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Send disease alert to all users in a specific location
        Includes reporting user information
        """
        try:
            disease_name = disease_data.get('disease', 'unknown')
            severity = disease_data.get('severity', 'medium')
            confidence = disease_data.get('confidence', 0.0)
            confidence_percent = confidence * 100
            
            # Ensure reporting_user is a dict
            if reporting_user is None:
                reporting_user = {}
            
            # Create notification based on severity
            if severity == 'critical':
                title = f"🚨 CRITICAL: {disease_name} in {location}!"
                body = f"Reported by {reporting_user.get('username', 'a farmer')}"
            elif severity == 'high':
                title = f"⚠️ URGENT: {disease_name} in {location}"
                body = f"Reported by {reporting_user.get('username', 'a farmer')}"
            else:
                title = f"🌱 {disease_name} in {location}"
                body = f"Reported by {reporting_user.get('username', 'a farmer')}"
            
            # Add confidence to body
            if confidence > 0:
                body += f" ({confidence_percent:.1f}% confidence)"
            
            # Prepare data with reporting user
            data = {
                'type': 'location_disease_alert',
                'disease': disease_name,
                'severity': severity,
                'confidence': str(confidence),
                'confidence_percent': f"{confidence_percent:.1f}%",
                'location': location,
                'treatment': disease_data.get('treatment', ''),
                'prevention': disease_data.get('prevention', ''),
                'timestamp': datetime.now().isoformat(),
                'reporting_user': reporting_user
            }
            
            return self.send_to_users_by_location(location, title, body, data)
            
        except Exception as e:
            logger.error(f"❌ Error sending disease alert to location: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def send_region_alert(
        self,
        region: str,
        disease_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Send disease alert to all users in a specific region
        (Alias for location-based method)
        """
        return self.send_disease_alert_by_location(region, disease_data, None)
    
    def send_to_multiple_devices(
        self,
        tokens: List[str],
        title: str,
        body: str,
        data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Send notification to multiple devices
        """
        try:
            total_success = 0
            total_failure = 0
            successful_users = []
            failed_users = []
            
            for i, token in enumerate(tokens):
                try:
                    message = messaging.Message(
                        notification=messaging.Notification(
                            title=title,
                            body=body,
                        ),
                        data=data or {},
                        token=token,
                        android=messaging.AndroidConfig(
                            priority="high",
                            notification=messaging.AndroidNotification(
                                channel_id='disease_alerts',
                                click_action='FLUTTER_NOTIFICATION_CLICK',
                                sound='default'
                            )
                        )
                    )
                    
                    response = messaging.send(message)
                    total_success += 1
                    successful_users.append({
                        'token_preview': token[:20] + '...'
                    })
                    
                except messaging.UnregisteredError:
                    logger.error(f"❌ Token {token[:20]}... is unregistered")
                    total_failure += 1
                    failed_users.append({
                        'token_preview': token[:20] + '...',
                        'error': 'Token unregistered'
                    })
                    self._handle_invalid_token(token)
                except Exception as e:
                    logger.error(f"❌ Error sending to {token[:20]}...: {e}")
                    total_failure += 1
                    failed_users.append({
                        'token_preview': token[:20] + '...',
                        'error': str(e)
                    })
            
            return {
                "status": "success",
                "total_tokens": len(tokens),
                "total_success": total_success,
                "total_failure": total_failure,
                "successful_users": successful_users,
                "failed_users": failed_users
            }
            
        except Exception as e:
            logger.error(f"❌ Error sending multiple messages: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def send_to_all_users(
        self,
        title: str,
        body: str,
        data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Send notification to ALL users who have FCM tokens
        """
        try:
            logger.info("📤 Sending notification to ALL users...")
            
            # Get all users with FCM tokens
            users_ref = self.db.collection('users')
            users = users_ref.get()
            
            if not users:
                logger.warning("⚠️ No users found with FCM tokens")
                return {
                    "status": "error",
                    "error": "No users found"
                }
            
            tokens = []
            user_ids = []
            user_names = []
            
            for user in users:
                user_data = user.to_dict()
                token = self._get_user_token(user_data)
                username = user_data.get('username', 'Unknown')
                
                # Validate token format
                if token and self.validate_token(token):
                    tokens.append(token)
                    user_ids.append(user.id)
                    user_names.append(username)
            
            if not tokens:
                logger.warning("⚠️ No valid FCM tokens found")
                return {
                    "status": "error",
                    "error": "No valid tokens"
                }
            
            logger.info(f"✅ Found {len(tokens)} users with valid tokens")
            
            # Send individually
            total_success = 0
            total_failure = 0
            successful_users = []
            failed_users = []
            
            for token, user_id, username in zip(tokens, user_ids, user_names):
                try:
                    message = messaging.Message(
                        notification=messaging.Notification(
                            title=title,
                            body=body,
                        ),
                        data={
                            'type': 'broadcast_alert',
                            'timestamp': datetime.now().isoformat(),
                            **(data or {})
                        },
                        token=token,
                        android=messaging.AndroidConfig(
                            priority="high",
                            notification=messaging.AndroidNotification(
                                channel_id='broadcast_alerts',
                                visibility='public',
                                sound='default',
                                click_action='FLUTTER_NOTIFICATION_CLICK',
                            )
                        ),
                        apns=messaging.APNSConfig(
                            headers={"apns-priority": "10"},
                            payload=messaging.APNSPayload(
                                aps=messaging.Aps(
                                    alert=messaging.ApsAlert(
                                        title=title,
                                        body=body
                                    ),
                                    sound='default',
                                    badge=1,
                                    content_available=True,
                                )
                            )
                        )
                    )
                    
                    response = messaging.send(message)
                    total_success += 1
                    logger.info(f"      ✅ Notification sent to {username}")
                    successful_users.append({
                        'user_id': user_id,
                        'username': username
                    })
                    
                except messaging.UnregisteredError:
                    logger.error(f"      ❌ Token invalid for user {username}")
                    total_failure += 1
                    failed_users.append({
                        'user_id': user_id,
                        'username': username,
                        'error': 'Token unregistered'
                    })
                    self._handle_invalid_token(token)
                except Exception as e:
                    logger.error(f"      ❌ Failed to send to {username}: {e}")
                    total_failure += 1
                    failed_users.append({
                        'user_id': user_id,
                        'username': username,
                        'error': str(e)
                    })
            
            return {
                "status": "success",
                "total_users": len(tokens),
                "total_success": total_success,
                "total_failure": total_failure,
                "successful_users": successful_users,
                "failed_users": failed_users,
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ Error sending to all users: {e}")
            return {
                "status": "error",
                "error": str(e)
            }

    def send_disease_alert_to_all(
        self,
        disease_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Send disease alert to ALL users
        """
        try:
            disease_name = disease_data.get('disease', 'unknown')
            severity = disease_data.get('severity', 'medium')
            confidence = disease_data.get('confidence', 0.0)
            confidence_percent = confidence * 100
            
            # Create notification based on severity
            if severity == 'critical':
                title = f"🚨 CRITICAL: {disease_name} Alert!"
                body = f"Immediate action needed! {disease_name} detected. Take precautions now."
            elif severity == 'high':
                title = f"⚠️ URGENT: {disease_name} Alert"
                body = f"{disease_name} reported. Take action soon to protect your crops."
            else:
                title = f"🌱 {disease_name} Alert"
                body = f"{disease_name} detected. Monitor your crops and take preventive measures."
            
            # Add confidence to body
            if confidence > 0:
                body += f" (Confidence: {confidence_percent:.1f}%)"
            
            data = {
                'type': 'broadcast_disease_alert',
                'disease': disease_name,
                'severity': severity,
                'confidence': str(confidence),
                'treatment': disease_data.get('treatment', ''),
                'prevention': disease_data.get('prevention', ''),
                'timestamp': datetime.now().isoformat()
            }
            
            return self.send_to_all_users(title, body, data)
            
        except Exception as e:
            logger.error(f"❌ Error sending disease alert to all: {e}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def _handle_invalid_token_by_user(self, user_id: str):
        """Remove invalid token for a specific user"""
        try:
            user_ref = self.db.collection('users').document(user_id)
            user_ref.update({
                'fcmToken': firestore.DELETE_FIELD,
                'tokenInvalidAt': firestore.SERVER_TIMESTAMP
            })
            logger.info(f"✅ Removed invalid token for user {user_id}")
        except Exception as e:
            logger.error(f"❌ Error removing token for user {user_id}: {e}")
    
    def _handle_invalid_token(self, token: str):
        """Handle invalid/unregistered token by removing from Firestore"""
        try:
            users_ref = self.db.collection('users')
            query = users_ref.where('fcmToken', '==', token).get()
            
            for doc in query:
                doc.reference.update({
                    'fcmToken': firestore.DELETE_FIELD,
                    'tokenInvalidAt': firestore.SERVER_TIMESTAMP
                })
                logger.info(f"✅ Removed invalid token from user {doc.id}")
                
        except Exception as e:
            logger.error(f"❌ Error handling invalid token: {e}")
    
    def _save_notification_history(self, user_id: str, title: str, body: str, disease_data: Dict, response: str):
        """Save notification to user's history"""
        try:
            notif_ref = self.db.collection('users').document(user_id) \
                               .collection('notifications').document()
            
            notif_ref.set({
                'id': notif_ref.id,
                'type': 'disease_alert',
                'title': title,
                'body': body,
                'disease': disease_data.get('disease'),
                'severity': disease_data.get('severity'),
                'confidence': disease_data.get('confidence'),
                'treatment': disease_data.get('treatment'),
                'prevention': disease_data.get('prevention'),
                'read': False,
                'createdAt': firestore.SERVER_TIMESTAMP,
                'fcmResponse': response
            })
            logger.info(f"✅ Notification saved to history for user {user_id}")
        except Exception as e:
            logger.error(f"❌ Error saving notification history: {e}")
    
    def _log_notification(self, token: str, title: str, body: str, data: Dict, status: str, response: str):
        """Log notification in Firestore for analytics"""
        try:
            users_ref = self.db.collection('users')
            query = users_ref.where('fcmToken', '==', token).limit(1).get()
            
            if query:
                user_id = query[0].id
                
                self.db.collection('notification_logs').add({
                    'userId': user_id,
                    'title': title,
                    'body': body,
                    'data': data,
                    'status': status,
                    'response': response,
                    'sentAt': firestore.SERVER_TIMESTAMP
                })
                logger.info(f"✅ Notification logged for analytics")
                
        except Exception as e:
            logger.error(f"❌ Error logging notification: {e}")


# Initialize service
fcm_service = FCMNotificationService()


# --------------------------------------------------
# Flask API Endpoints
# --------------------------------------------------

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint with detailed status"""
    try:
        # Test Firestore connection
        test_collection = db.collection('_health_check').limit(1).get()
        firestore_status = "connected"
    except Exception as e:
        firestore_status = f"error: {e}"
    
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "firebase_initialized": bool(firebase_admin._apps),
        "firestore_status": firestore_status,
        "sender_id": os.getenv("FIREBASE_SENDER_ID", "70868825299"),
        "project_id": os.getenv("FIREBASE_PROJECT_ID"),
        "credential_file": os.path.basename(cred_path)
    })


@app.route('/api/disease-alert', methods=['POST'])
def disease_alert():
    """
    Send disease detection alert to a specific user
    Expected JSON:
    {
        "userId": "user_firebase_uid",
        "disease": "early_blight",
        "severity": "high",
        "confidence": 0.92,
        "treatment": "Apply fungicide",
        "prevention": "Improve air circulation"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        logger.info(f"📨 Received disease alert request: {json.dumps(data, indent=2)}")
        
        required_fields = ['userId', 'disease', 'severity']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        disease_data = {
            'disease': data['disease'],
            'severity': data['severity'],
            'confidence': data.get('confidence', 0.0),
            'treatment': data.get('treatment', ''),
            'prevention': data.get('prevention', '')
        }
        
        result = fcm_service.send_disease_alert(
            user_id=data['userId'],
            disease_data=disease_data
        )
        
        if result['status'] == 'success':
            logger.info(f"✅ Alert sent successfully")
            return jsonify(result), 200
        else:
            logger.error(f"❌ Alert failed: {result.get('error')}")
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"❌ API Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/send-notification', methods=['POST'])
def send_notification():
    """
    Send notification to a single device
    Expected JSON:
    {
        "token": "device_fcm_token",
        "title": "Notification Title",
        "body": "Notification Body",
        "data": {"key": "value"} (optional)
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        required_fields = ['token', 'title', 'body']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        result = fcm_service.send_to_device(
            token=data['token'],
            title=data['title'],
            body=data['body'],
            data=data.get('data', {})
        )
        
        if result['status'] == 'success':
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"❌ API Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/send-topic', methods=['POST'])
def send_topic_notification():
    """
    Send notification to a topic
    Expected JSON:
    {
        "topic": "topic_name",
        "title": "Notification Title",
        "body": "Notification Body",
        "data": {"key": "value"} (optional)
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        required_fields = ['topic', 'title', 'body']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        result = fcm_service.send_to_topic(
            topic=data['topic'],
            title=data['title'],
            body=data['body'],
            data=data.get('data', {})
        )
        
        if result['status'] == 'success':
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"❌ API Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/location-alert', methods=['POST'])
def location_alert():
    """
    Send disease alert to users in a specific location
    Expected JSON:
    {
        "location": "Salem",
        "disease": "early_blight",
        "severity": "high",
        "confidence": 0.92,
        "treatment": "Apply fungicide",
        "prevention": "Improve air circulation",
        "reporting_user": {
            "user_id": "user123",
            "username": "Jerry"
        }
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        logger.info(f"📨 Received location alert request: {json.dumps(data, indent=2)}")
        
        required_fields = ['location', 'disease', 'severity']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        disease_data = {
            'disease': data['disease'],
            'severity': data['severity'],
            'confidence': data.get('confidence', 0.0),
            'treatment': data.get('treatment', ''),
            'prevention': data.get('prevention', '')
        }
        
        reporting_user = data.get('reporting_user')
        
        result = fcm_service.send_disease_alert_by_location(
            location=data['location'],
            disease_data=disease_data,
            reporting_user=reporting_user
        )
        
        if result['status'] == 'success':
            logger.info(f"✅ Location alert sent to {result['total_users']} users in {data['location']}")
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"❌ API Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/region-alert', methods=['POST'])
def region_alert():
    """
    Send disease alert to all users in a region
    Expected JSON:
    {
        "region": "Salem",
        "disease": "early_blight",
        "severity": "high"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        required_fields = ['region', 'disease', 'severity']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        disease_data = {
            'disease': data['disease'],
            'severity': data['severity'],
            'confidence': data.get('confidence', 0.0),
            'treatment': data.get('treatment', ''),
            'prevention': data.get('prevention', '')
        }
        
        result = fcm_service.send_region_alert(
            region=data['region'],
            disease_data=disease_data
        )
        
        if result['status'] == 'success':
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"❌ API Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/send-bulk', methods=['POST'])
def send_bulk_notifications():
    """
    Send notifications to multiple devices
    Expected JSON:
    {
        "tokens": ["token1", "token2", ...],
        "title": "Notification Title",
        "body": "Notification Body",
        "data": {"key": "value"} (optional)
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        required_fields = ['tokens', 'title', 'body']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        if not isinstance(data['tokens'], list):
            return jsonify({"error": "Tokens must be a list"}), 400
        
        result = fcm_service.send_to_multiple_devices(
            tokens=data['tokens'],
            title=data['title'],
            body=data['body'],
            data=data.get('data', {})
        )
        
        if result['status'] == 'success':
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"❌ API Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/broadcast', methods=['POST'])
def broadcast_notification():
    """
    Send notification to ALL users
    Expected JSON:
    {
        "title": "Notification Title",
        "body": "Notification Body",
        "data": {"key": "value"} (optional)
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        required_fields = ['title', 'body']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        result = fcm_service.send_to_all_users(
            title=data['title'],
            body=data['body'],
            data=data.get('data', {})
        )
        
        if result['status'] == 'success':
            logger.info(f"✅ Broadcast sent to {result['total_users']} users")
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"❌ API Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/broadcast-disease', methods=['POST'])
def broadcast_disease_alert():
    """
    Send disease alert to ALL users
    Expected JSON:
    {
        "disease": "early_blight",
        "severity": "high",
        "confidence": 0.92,
        "treatment": "Apply fungicide",
        "prevention": "Improve air circulation"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        required_fields = ['disease', 'severity']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        disease_data = {
            'disease': data['disease'],
            'severity': data['severity'],
            'confidence': data.get('confidence', 0.0),
            'treatment': data.get('treatment', ''),
            'prevention': data.get('prevention', '')
        }
        
        result = fcm_service.send_disease_alert_to_all(disease_data)
        
        if result['status'] == 'success':
            logger.info(f"✅ Disease broadcast sent to {result['total_users']} users")
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        logger.error(f"❌ API Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/user-stats', methods=['GET'])
def get_user_stats():
    """Get statistics about users with FCM tokens based on your schema"""
    try:
        users_ref = db.collection('users')
        users = users_ref.get()
        
        total_users = len(users)
        valid_tokens = 0
        location_stats = {}
        subscription_stats = {'free': 0, 'premium': 0, 'unknown': 0}
        
        for user in users:
            user_data = user.to_dict()
            token = fcm_service._get_user_token(user_data)
            
            if token and len(token) > 50:
                valid_tokens += 1
            
            # Count by location (matches your schema)
            location = user_data.get('location', 'unknown')
            location_stats[location] = location_stats.get(location, 0) + 1
            
            # Count by subscription (matches your schema)
            subscription = user_data.get('subscription', 'free')
            if subscription in subscription_stats:
                subscription_stats[subscription] += 1
            else:
                subscription_stats['unknown'] += 1
        
        # Get platform stats (from your schema)
        platform_stats = {'mobile': 0, 'unknown': 0}
        for user in users:
            user_data = user.to_dict()
            platform = user_data.get('platform', 'unknown')
            if platform in platform_stats:
                platform_stats[platform] += 1
            else:
                platform_stats['unknown'] += 1
        
        return jsonify({
            "total_users_in_db": total_users,
            "users_with_valid_tokens": valid_tokens,
            "location_distribution": location_stats,
            "subscription_distribution": subscription_stats,
            "platform_distribution": platform_stats,
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Error getting user stats: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/validate-token', methods=['POST'])
def validate_token():
    """
    Validate an FCM token
    Expected JSON:
    {
        "token": "device_fcm_token"
    }
    """
    try:
        data = request.get_json()
        
        if not data or 'token' not in data:
            return jsonify({"error": "Token not provided"}), 400
        
        is_valid = fcm_service.validate_token(data['token'])
        
        return jsonify({
            "token": data['token'][:20] + "...",
            "is_valid": is_valid,
            "format_ok": True
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Validation Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/user/<user_id>/token', methods=['GET'])
def get_user_token_status(user_id):
    """Check if user has a valid FCM token"""
    try:
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get()
        
        if not user_doc.exists:
            return jsonify({"error": "User not found"}), 404
        
        user_data = user_doc.to_dict()
        has_token = 'fcmToken' in user_data or 'fcToken' in user_data
        
        return jsonify({
            "user_id": user_id,
            "has_token": has_token,
            "token_updated": user_data.get('fcmTokenUpdatedAt', None) is not None,
            "location": user_data.get('location', 'unknown'),
            "subscription": user_data.get('subscription', 'free'),
            "username": user_data.get('username', 'unknown')
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Error checking user token: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/debug/user/<user_id>', methods=['GET'])
def debug_user(user_id):
    """Debug endpoint to check user configuration"""
    try:
        user_ref = db.collection('users').document(user_id)
        user_doc = user_ref.get()
        
        if not user_doc.exists:
            return jsonify({"error": "User not found"}), 404
        
        user_data = user_doc.to_dict()
        
        # Get token info
        token = fcm_service._get_user_token(user_data)
        has_token = token is not None
        token_preview = token[:20] + '...' if has_token else None
        
        # Check if token is valid
        token_valid = None
        if has_token:
            try:
                token_valid = fcm_service.validate_token(token)
            except:
                token_valid = False
        
        return jsonify({
            "user_id": user_id,
            "exists": True,
            "has_token": has_token,
            "token_preview": token_preview,
            "token_valid_format": token_valid,
            "token_updated": user_data.get('fcmTokenUpdatedAt', None) is not None,
            "location": user_data.get('location', 'unknown'),
            "subscription": user_data.get('subscription', 'free'),
            "username": user_data.get('username', 'unknown'),
            "phoneNumber": user_data.get('phoneNumber', 'unknown'),
            "platform": user_data.get('platform', 'unknown'),
            "lastActive": str(user_data.get('lastActive', 'unknown')),
            "updatedAt": str(user_data.get('updatedAt', 'unknown'))
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Debug error: {e}")
        return jsonify({"error": str(e)}), 500


# --------------------------------------------------
# Command line interface for testing
# --------------------------------------------------
def test_notification():
    """Test function for command line usage"""
    print("\n=== 🌱 FCM Disease Alert Test ===\n")
    
    print("📊 Select notification type:")
    print("1. Send to single user (requires UID)")
    print("2. Send to all users in a specific location")
    print("3. Send to ALL users (broadcast)")
    
    choice = input("\nEnter choice (1-3): ").strip()
    
    if choice not in ['1', '2', '3']:
        print("❌ Invalid choice")
        return
    
    # Get disease severity choice
    print("\n📊 Select disease severity:")
    print("1. Medium (🌱 Leaf Spot)")
    print("2. High (⚠️ Early Blight)")
    print("3. Critical (🚨 Late Blight)")
    
    severity_choice = input("Enter choice (1-3): ").strip()
    
    test_cases = {
        '1': {
            'disease': 'leaf_spot',
            'severity': 'medium',
            'confidence': 0.78,
            'treatment': 'Apply fungicide and remove affected leaves',
            'prevention': 'Water at base of plants, maintain spacing'
        },
        '2': {
            'disease': 'early_blight',
            'severity': 'high',
            'confidence': 0.92,
            'treatment': 'Apply fungicide containing chlorothalonil',
            'prevention': 'Ensure proper air circulation, avoid overhead watering'
        },
        '3': {
            'disease': 'late_blight',
            'severity': 'critical',
            'confidence': 0.95,
            'treatment': 'Remove infected plants immediately, apply copper-based fungicide',
            'prevention': 'Use resistant varieties, avoid wet foliage'
        }
    }
    
    test_case = test_cases.get(severity_choice, test_cases['2'])
    
    # Handle based on user choice
    if choice == '1':
        # Send to single user - require UID
        user_id = input("\nEnter user ID (Firebase UID): ").strip()
        if not user_id:
            print("❌ User ID cannot be empty")
            return
        
        print(f"\n🔍 Checking user {user_id}...")
        
        try:
            user_ref = db.collection('users').document(user_id)
            user_doc = user_ref.get()
            
            if not user_doc.exists:
                print(f"❌ User {user_id} not found in Firestore")
                return
            
            user_data = user_doc.to_dict()
            token = fcm_service._get_user_token(user_data)
            has_token = token is not None
            
            if not has_token:
                print(f"❌ No FCM token found for user {user_id}")
                print(f"📄 User data: {user_data}")
                return
            
            print(f"✅ Found token: {token[:30]}...")
            print(f"📅 Token updated: {user_data.get('fcmTokenUpdatedAt')}")
            print(f"📍 Location: {user_data.get('location', 'unknown')}")
            print(f"👤 Username: {user_data.get('username', 'unknown')}")
            
        except Exception as e:
            print(f"❌ Error checking user: {e}")
            return
        
        print(f"\n📤 Sending {test_case['severity'].upper()} alert to user {user_id}...")
        result = fcm_service.send_disease_alert(
            user_id=user_id,
            disease_data=test_case
        )
        
    elif choice == '2':
        # Send to location - ask for location
        location = input("\nEnter location name (e.g., Salem, Coimbatore): ").strip()
        if not location:
            print("❌ Location cannot be empty")
            return
        
        # First check how many users are in this location
        print(f"\n🔍 Checking users in location '{location}'...")
        try:
            users_ref = db.collection('users')
            users = users_ref.where('location', '==', location).get()
            
            total_in_location = len(users)
            users_with_tokens = 0
            
            for user in users:
                user_data = user.to_dict()
                token = fcm_service._get_user_token(user_data)
                if token and fcm_service.validate_token(token):
                    users_with_tokens += 1
            
            print(f"📍 Location: {location}")
            print(f"📊 Total users in location: {total_in_location}")
            print(f"✅ Users with valid tokens: {users_with_tokens}")
            
            if users_with_tokens == 0:
                print("\n⚠️ No users with valid tokens in this location!")
                proceed = input("Do you still want to send? (y/n): ").strip().lower()
                if proceed != 'y':
                    print("❌ Notification cancelled")
                    return
                    
        except Exception as e:
            print(f"⚠️ Could not check location stats: {e}")
        
        print(f"\n📤 Sending {test_case['severity'].upper()} alert to all users in {location}...")
        result = fcm_service.send_disease_alert_by_location(
            location=location,
            disease_data=test_case,
            reporting_user={'username': 'CLI Test', 'user_id': 'test'}
        )
        
    else:  # choice == '3'
        # Send to ALL users - no questions asked
        print("\n📤 Sending disease alert to ALL users...")
        
        # First check total users
        try:
            users_ref = db.collection('users')
            users = users_ref.get()
            total_users = 0
            for user in users:
                user_data = user.to_dict()
                token = fcm_service._get_user_token(user_data)
                if token and fcm_service.validate_token(token):
                    total_users += 1
            print(f"📊 Total users with tokens: {total_users}")
            
            if total_users == 0:
                print("⚠️ No users with tokens found!")
                proceed = input("Continue anyway? (y/n): ").strip().lower()
                if proceed != 'y':
                    print("❌ Broadcast cancelled")
                    return
                    
        except Exception as e:
            print(f"⚠️ Could not get user stats: {e}")
        
        result = fcm_service.send_disease_alert_to_all(test_case)
    
    # Display results
    print("\n📱 Result:")
    print(json.dumps(result, indent=2))
    
    if result['status'] == 'success':
        print("\n✅ Alert sent successfully!")
        if 'total_users' in result:
            print(f"   📊 Total users targeted: {result['total_users']}")
        if 'total_success' in result:
            print(f"   ✅ Successful: {result['total_success']}")
        if 'total_failure' in result:
            print(f"   ❌ Failed: {result['total_failure']}")
        
        # Show list of users who received (if available)
        if 'successful_users' in result and result['successful_users']:
            print("\n   📋 Users who received notification:")
            for user in result['successful_users'][:10]:  # Show first 10
                print(f"      - {user.get('username', 'Unknown')} (ID: {user.get('user_id', '')[:8]}...)")
            if len(result['successful_users']) > 10:
                print(f"      ... and {len(result['successful_users']) - 10} more")
        
        # Show reporting user if available
        if 'reporting_user' in result:
            print(f"\n   👤 Reported by: {result['reporting_user']}")
        
        # Show failed users if any
        if 'failed_users' in result and result['failed_users']:
            print("\n   ❌ Users who failed:")
            for user in result['failed_users'][:5]:  # Show first 5
                print(f"      - {user.get('username', 'Unknown')}: {user.get('error', 'Unknown error')}")
            if len(result['failed_users']) > 5:
                print(f"      ... and {len(result['failed_users']) - 5} more")
                
    else:
        print(f"\n❌ Failed: {result.get('error', 'Unknown error')}")
        if result.get('error_code') == 'unregistered':
            print("\n💡 This token is no longer valid. Please:")
            print("1. Uninstall and reinstall the Flutter app")
            print("2. Get a new token from the app")
            print("3. Update the token in your test")


if __name__ == "__main__":
    import sys
    
    # Check if running in CLI test mode
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        test_notification()
    else:
        # Run Flask server
        port = int(os.getenv("PORT", 5000))
        debug = os.getenv("FLASK_DEBUG", "False").lower() == "true"
        
        print(f"🚀 FCM Notification Server starting on port {port}")
        print(f"📝 Debug mode: {debug}")
        print(f"🔥 Firebase: {'✅ Initialized' if firebase_admin._apps else '❌ Not initialized'}")
        print(f"📱 Sender ID: {os.getenv('FIREBASE_SENDER_ID', '70868825299')}")
        print(f"📁 Credential file: {os.path.basename(cred_path)}")
        print("\n📋 Available endpoints:")
        print("  POST /api/disease-alert        - Send disease alert to specific user")
        print("  POST /api/send-notification    - Send to single device")
        print("  POST /api/send-topic           - Send to topic")
        print("  POST /api/location-alert        - Send alert to users in a location (with reporter info)")
        print("  POST /api/region-alert          - Send alert to region (alias)")
        print("  POST /api/send-bulk            - Send to multiple devices")
        print("  POST /api/broadcast             - Send to ALL users (general)")
        print("  POST /api/broadcast-disease     - Send disease alert to ALL users")
        print("  GET  /api/user-stats            - Get user statistics")
        print("  POST /api/validate-token       - Validate token")
        print("  GET  /api/user/<id>/token      - Check user token status")
        print("  GET  /api/debug/user/<id>      - Debug user configuration")
        print("  GET  /health                    - Health check")
        print("\n📝 To test with CLI: python python_fcm_server.py --test")
        
        app.run(host='0.0.0.0', port=port, debug=debug)