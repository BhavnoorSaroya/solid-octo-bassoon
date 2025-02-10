from flask import Flask, request, jsonify
import json, os, threading, ast, requests
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
import openai
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# Slack setup
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
signature_verifier = SignatureVerifier(os.getenv("SLACK_SIGNING_SECRET"))

# OpenAI and Trello setup
openai.api_key = os.getenv("OPENAI_API_KEY")
TRELLO_API_KEY = os.getenv("TRELLO_API_KEY")
TRELLO_API_TOKEN = os.getenv("TRELLO_API_TOKEN")
TRELLO_API_BASE = "https://api.trello.com/1"
TRELLO_BOARD_ID = os.getenv("TRELLO_BOARD_ID")

@app.route("/slack/events", methods=["POST"])
def slack_events():

    print("Content-Type:", request.content_type)
    print("Raw Data:", request.data)
    # First, check if it's JSON (i.e. an event payload) or form data (i.e. slash command)
    if request.content_type and request.content_type.startswith("application/json"):
        payload = request.get_json()
        print("Combined endpoint received JSON payload:", payload)
        # Handle URL verification challenge
        if "challenge" in payload:
            return jsonify({"challenge": payload["challenge"]}), 200
        # Assume it's a message event
        event = payload.get("event", {})
        # Skip bot messages
        if event.get("subtype") == "bot_message":
            return jsonify({"status": "ignored"}), 200
        # Process only messages that are in a thread
        if event.get("thread_ts") and event.get("text"):
            channel = event.get("channel")
            thread_ts = event.get("thread_ts")
            user_text = event.get("text").strip()
            user_id = event.get("user", "")
            print("Received thread message:", user_text, "in thread:", thread_ts)
            threading.Thread(
                target=handle_message,
                args=(user_id, user_text, channel, thread_ts)
            ).start()
        return jsonify({"status": "ok"}), 200

    else:
        # Otherwise, assume form data (slash command)
        data = request.form.to_dict()
        print("Combined endpoint received form data:", data)
        # Handle URL verification challenge if present (rare for slash commands)
        if "challenge" in data:
            return jsonify({"challenge": data["challenge"]}), 200

        if data and "channel_id" in data:
            channel_id = data["channel_id"]
            user_text = data.get("text", "").strip()

            try:
                # Post a parent message to create the thread.
                init_resp = slack_client.chat_postMessage(
                    channel=channel_id,
                    text="Thread started—please type your Trello command below."
                )
                thread_ts = init_resp["ts"]
                print("Thread initiated with ts:", thread_ts)
                # (Optional) Post a dummy reply so the thread is visible.
                slack_client.chat_postMessage(
                    channel=channel_id,
                    text="Thread initiated. Please continue your commands here.",
                    thread_ts=thread_ts
                )
            except Exception as e:
                print(f"Error creating thread: {e}")
                return jsonify({"text": "Error starting thread"}), 500

            # If the slash command includes text, process it immediately.
            if user_text:
                threading.Thread(
                    target=handle_message,
                    args=(data.get("user_id", ""), user_text, channel_id, thread_ts)
                ).start()

            return jsonify({
                "response_type": "in_channel",
                "text": "Thread started—continue your commands in the thread below."
            }), 200

        return jsonify({"text": "No data received"}), 200

def handle_message(user_id, text, channel, thread_ts):
    system_prompt = (
        "You are ClutchAI, a Slack bot that manipulates a Trello board. "
        "All commands are provided in this thread. Use the conversation context and current Trello board data "
        "to determine the necessary Trello API actions, and reply with both the action details and a natural language response. "
        "Current board data: " + get_latest_board_data()
    )
    try:
        response = openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ]
        )
    except Exception as e:
        send_slack_message(f"Error processing your request: {str(e)}", channel, thread_ts)
        return

    try:
        gpt_response = ast.literal_eval(response.choices[0].message.content)
    except Exception as e:
        send_slack_message(f"Error parsing AI response: {str(e)}", channel, thread_ts)
        return

    api_action = gpt_response.get('api_action')
    user_response = gpt_response.get('response', "No response provided.")

    if api_action:
        execute_trello_action(api_action)

    send_slack_message(user_response, channel, thread_ts)

def execute_trello_action(action):
    endpoint = action['endpoint']
    method = action['method']
    parameters = action['parameters']
    url_params = action.get('url_params', "?")
    parameters.update({
        "key": TRELLO_API_KEY,
        "token": TRELLO_API_TOKEN
    })
    url = f"{TRELLO_API_BASE}{endpoint}{url_params}&key={TRELLO_API_KEY}&token={TRELLO_API_TOKEN}"
    if method == "POST":
        res = requests.post(url, json=parameters)
    elif method == "PUT":
        res = requests.put(url, json=parameters)
    elif method == "DELETE":
        res = requests.delete(url, params=parameters)
    print("Trello action status:", res.status_code)

def get_latest_board_data():
    board_id = TRELLO_BOARD_ID
    url_cards = f"{TRELLO_API_BASE}/boards/{board_id}/cards?key={TRELLO_API_KEY}&token={TRELLO_API_TOKEN}"
    boards = requests.get(url_cards).json()
    url_lists = f"{TRELLO_API_BASE}/boards/{board_id}/lists?key={TRELLO_API_KEY}&token={TRELLO_API_TOKEN}"
    lists = requests.get(url_lists).json()
    return str({"cards": boards, "lists": lists})

def send_slack_message(text, channel, thread_ts=None):
    try:
        response = slack_client.chat_postMessage(
            channel=channel,
            text=text,
            thread_ts=thread_ts
        )
        print("Message sent with ts:", response["ts"])
    except Exception as e:
        print("Error sending message:", e)

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(port=5000, host="0.0.0.0", debug=True)
