"""
Notifications — Send task result alerts to frontend.

WHAT THIS DOES:
===============
After executor finishes a task, we need to tell the frontend what happened.

Example:
  ✅ "Arm 1 completed 'pick' successfully (Confidence: 0.95)"
  ❌ "Arm 1 FAILED: Motor timeout"
  ⏳ "Arm 1 is processing task..."

These messages appear in the browser in real-time.

WHY WE NEED IT:
===============
Without notifications:
  - Frontend doesn't know if task succeeded
  - User stares at screen confused
  - No feedback about arm status
  - No visibility into what's happening

With notifications:
  - Frontend shows live updates
  - User sees exactly what each arm is doing
  - Errors are visible immediately
  - Complete visibility into system

COMMUNICATION:
==============
We use WebSocket (socketio) to send data to frontend in REAL-TIME.
Not HTTP (request-response), but push notifications.
Frontend listens and updates UI instantly.
"""

from datetime import datetime
from app import socketio  # WebSocket connection to frontend


class TaskNotification:
    """
    Send task status notifications to frontend.

    This class handles all notification types:
    - Task started (⏳ processing)
    - Task succeeded (✅ completed)
    - Task failed (❌ error)
    - Status updates (real-time)
    """

    # ========================================================================
    # NOTIFICATION 1: TASK PROCESSING (Task just started)
    # ========================================================================

    @staticmethod
    def notify_processing(arm_id: int, action: str):
        """
        Send: "⏳ Arm N is [action]..."

        When: Task just started, arm is processing

        Example:
          notify_processing(1, "picking")
          → "⏳ Arm 1 is picking..."

        Args:
            arm_id: Which arm? (1, 2, or 3)
            action: What is it doing? ("picking", "moving", "dropping")
        """

        # Get current time for timestamp
        timestamp = datetime.now().strftime('%H:%M:%S')

        # WHAT TO SEND TO FRONTEND:
        # This data will appear in the browser
        notification_data = {
            "status": "processing",      # Task is in progress
            "arm_id": arm_id,            # Which arm
            "action": action,            # What it's doing
            "timestamp": timestamp,      # When did this happen?
            "message": f"⏳ Arm {arm_id} is {action}...",  # Display text
            "emoji": "⏳"                # Visual indicator
        }

        # Send to frontend via WebSocket
        # All browsers listening to 'task_status' receive this
        socketio.emit('task_status', notification_data)

        print(f"[NOTIFICATION] {notification_data['message']}")

    # ========================================================================
    # NOTIFICATION 2: TASK SUCCESS (Task completed successfully)
    # ========================================================================

    @staticmethod
    def notify_success(arm_id: int, action: str, confidence: float = 1.0):
        """
        Send: "✅ Arm N completed [action] successfully"

        When: Task finished without errors

        Example:
          notify_success(1, "pick", 0.95)
          → "✅ Arm 1 completed 'pick' successfully (Confidence: 95%)"

        Args:
            arm_id: Which arm completed the task? (1, 2, or 3)
            action: What did it do? ("pick", "move", "drop")
            confidence: How sure was Qwen? (0.0 = not sure, 1.0 = very sure)
        """

        # Get current time
        timestamp = datetime.now().strftime('%H:%M:%S')

        # MAIN NOTIFICATION (for task completion display)
        task_complete_data = {
            "status": "success",                              # Task succeeded
            "arm_id": arm_id,                                # Which arm
            "action": action,                                # What it did
            "confidence": confidence,                        # Qwen's confidence
            "timestamp": timestamp,                         # When?
            "message": f"✅ Arm {arm_id} completed '{action}' successfully",
            "emoji": "✅",                                   # Visual indicator
            "details": f"Confidence: {confidence*100:.1f}%"  # Extra info
        }

        # Send to frontend
        # Browsers listening to 'task_complete' receive this
        socketio.emit('task_complete', task_complete_data)

        # ALSO LOG IT (for the log panel)
        # The log panel shows a detailed history of all actions
        log_data = {
            'timestamp': timestamp,
            'source': f'arm_{arm_id}',                      # Which arm?
            'message': f'✅ Task completed: {action}',      # What happened?
            'level': 'success'                              # Success level
        }

        # Send log entry to frontend
        socketio.emit('log_message', log_data)

        # Also print to console (for debugging)
        print(f"[SUCCESS] Arm {arm_id}: {action} completed (Confidence: {confidence})")

    # ========================================================================
    # NOTIFICATION 3: TASK FAILURE (Task failed, something went wrong)
    # ========================================================================

    @staticmethod
    def notify_failure(arm_id: int, reason: str, action: str = "unknown"):
        """
        Send: "❌ Arm N FAILED: [reason]"

        When: Task encountered an error

        Example:
          notify_failure(1, "Motor timeout", "pick")
          → "❌ Arm 1 FAILED: Motor timeout"

        Args:
            arm_id: Which arm failed? (1, 2, or 3)
            reason: Why did it fail? ("Motor timeout", "Connection lost", etc.)
            action: What was it trying to do? (for context)
        """

        # Get current time
        timestamp = datetime.now().strftime('%H:%M:%S')

        # MAIN NOTIFICATION (for task failure display)
        task_complete_data = {
            "status": "failed",                            # Task FAILED
            "arm_id": arm_id,                             # Which arm
            "action": action,                             # What was it doing?
            "reason": reason,                             # Why did it fail?
            "timestamp": timestamp,                       # When?
            "message": f"❌ Arm {arm_id} FAILED: {reason}",
            "emoji": "❌",                                 # Visual indicator
            "details": f"Task: {action}"                  # What was being done?
        }

        # Send to frontend
        # Browsers listening to 'task_complete' receive this
        socketio.emit('task_complete', task_complete_data)

        # ALSO LOG IT (for the log panel)
        log_data = {
            'timestamp': timestamp,
            'source': f'arm_{arm_id}',
            'message': f'❌ Task failed: {reason}',
            'level': 'error'
        }

        # Send log entry to frontend
        socketio.emit('log_message', log_data)

        # Also print to console (for debugging)
        print(f"[FAILURE] Arm {arm_id}: {action} failed - {reason}")

    # ========================================================================
    # NOTIFICATION 4: ARM HEALTH (Arm status changed)
    # ========================================================================

    @staticmethod
    def notify_arm_health(arm_id: int, health_status: str, error: str = None):
        """
        Send: "Arm N health changed to [status]"

        When: Arm health status changes (alive, unhealthy, dead, stopped)

        Example:
          notify_arm_health(1, "unhealthy", "Motor error detected")
          → "Arm 1 is unhealthy: Motor error detected"

        Args:
            arm_id: Which arm? (1, 2, or 3)
            health_status: New health status
                          ("alive", "unhealthy", "dead", "stopped", "idle")
            error: Why is health bad? (optional error message)
        """

        # Get current time
        timestamp = datetime.now().strftime('%H:%M:%S')

        # Determine emoji and color based on health
        if health_status == "alive" or health_status == "idle":
            emoji = "✅"
            message = f"✅ Arm {arm_id} is healthy"
        elif health_status == "unhealthy":
            emoji = "⚠️"
            message = f"⚠️ Arm {arm_id} is unhealthy: {error}"
        elif health_status == "dead":
            emoji = "🔴"
            message = f"🔴 Arm {arm_id} is DEAD: {error}"
        elif health_status == "stopped":
            emoji = "⏹️"
            message = f"⏹️ Arm {arm_id} was stopped: {error}"
        else:
            emoji = "❓"
            message = f"❓ Arm {arm_id} status: {health_status}"

        # Send health update
        health_data = {
            "arm_id": arm_id,
            "health": health_status,
            "error": error,
            "timestamp": timestamp,
            "message": message,
            "emoji": emoji
        }

        # Send to frontend
        socketio.emit('arm_health', health_data)

        # Also log it
        log_data = {
            'timestamp': timestamp,
            'source': f'arm_{arm_id}',
            'message': f'{emoji} Health: {health_status}' + (f' - {error}' if error else ''),
            'level': 'warning' if health_status == 'unhealthy' else 'error' if health_status == 'dead' else 'info'
        }

        socketio.emit('log_message', log_data)

        print(f"[HEALTH] Arm {arm_id}: {health_status}" + (f" - {error}" if error else ""))

    # ========================================================================
    # NOTIFICATION 5: DECISION MADE (Qwen made a decision)
    # ========================================================================

    @staticmethod
    def notify_decision(arm_id: int, decision: dict):
        """
        Send: "🧠 Arm N brain decided: [action] at [angle]°"

        When: Qwen brain makes a decision

        Shows Qwen's reasoning to the user

        Example:
          notify_decision(1, {
            "action": "pick",
            "target_angle": 45,
            "confidence": 0.95,
            "reasoning": "Object at intake..."
          })

        Args:
            arm_id: Which arm's brain? (1, 2, or 3)
            decision: The decision dict from brain
                     {action, target_angle, confidence, reasoning}
        """

        # Get current time
        timestamp = datetime.now().strftime('%H:%M:%S')

        # Create notification showing Qwen's decision
        decision_data = {
            "arm_id": arm_id,
            "action": decision.get('action'),
            "target_angle": decision.get('target_angle'),
            "confidence": decision.get('confidence'),
            "reasoning": decision.get('reasoning'),
            "timestamp": timestamp,
            "message": f"🧠 Arm {arm_id} brain decided: {decision.get('action')} at {decision.get('target_angle')}°",
            "emoji": "🧠"
        }

        # Send to frontend (if you want to show brain decisions in UI)
        socketio.emit('brain_decision', decision_data)

        # Log it
        log_data = {
            'timestamp': timestamp,
            'source': f'arm_{arm_id}_brain',
            'message': f'🧠 Decision: {decision.get("action")} @ {decision.get("target_angle")}° (confidence: {decision.get("confidence")})',
            'level': 'info'
        }

        socketio.emit('log_message', log_data)

        print(f"[DECISION] Arm {arm_id}: {decision.get('action')} at {decision.get('target_angle')}°")


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================
# Keep one notification service
# Can be used from anywhere in the system

_notifications = TaskNotification()


def get_notification_service() -> TaskNotification:
    """
    Get the notification service.

    Can be used from anywhere to send notifications.

    Example:
        notif = get_notification_service()
        notif.notify_success(1, "pick", 0.95)
        → Sends notification to frontend

    Returns:
        The TaskNotification instance (singleton)
    """
    return _notifications


# ============================================================================
# HELPER FUNCTION: Quick notification
# ============================================================================

def send_notification(notification_type: str, **kwargs):
    """
    Quick way to send any notification.

    Instead of:
      get_notification_service().notify_success(...)

    You can do:
      send_notification("success", arm_id=1, action="pick", confidence=0.95)

    Args:
        notification_type: "processing", "success", "failure", "health", "decision"
        **kwargs: Arguments for the notification

    Example:
        send_notification("success", arm_id=1, action="pick", confidence=0.95)
        send_notification("failure", arm_id=2, reason="Timeout", action="move")
    """

    notif = get_notification_service()

    if notification_type == "processing":
        notif.notify_processing(kwargs.get('arm_id'), kwargs.get('action'))

    elif notification_type == "success":
        notif.notify_success(
            kwargs.get('arm_id'),
            kwargs.get('action'),
            kwargs.get('confidence', 1.0)
        )

    elif notification_type == "failure":
        notif.notify_failure(
            kwargs.get('arm_id'),
            kwargs.get('reason'),
            kwargs.get('action', 'unknown')
        )

    elif notification_type == "health":
        notif.notify_arm_health(
            kwargs.get('arm_id'),
            kwargs.get('health_status'),
            kwargs.get('error')
        )

    elif notification_type == "decision":
        notif.notify_decision(
            kwargs.get('arm_id'),
            kwargs.get('decision')
        )

    else:
        print(f"[WARNING] Unknown notification type: {notification_type}")
