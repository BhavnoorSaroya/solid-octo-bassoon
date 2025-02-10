from flask import Flask, request, jsonify
import json
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
import openai 
import requests
import os
from dotenv import load_dotenv
import threading 
import ast 

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)

# Slack setup (ensure these tokens are current and belong to an active bot)
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
signature_verifier = SignatureVerifier(os.getenv("SLACK_SIGNING_SECRET"))

# OpenAI and Trello setup remain the same
openai.api_key = os.getenv("OPENAI_API_KEY")
TRELLO_API_KEY = os.getenv("TRELLO_API_KEY")
TRELLO_API_TOKEN = os.getenv("TRELLO_API_TOKEN")
TRELLO_API_BASE = "https://api.trello.com/1"
TRELLO_BOARD_ID = os.getenv("TRELLO_BOARD_ID")

# Slash command endpoint for /clutchAI
@app.route("/slack/events", methods=["POST"])
def slack_events():
    # (Optional) Verify the request signature:
    # if not signature_verifier.is_valid_request(request.get_data(), request.headers):
    #     return "Invalid request signature", 403

    data = request.form

    # Check that we received a slash command payload
    if data and "channel_id" in data:
        channel_id = data["channel_id"]
        user_text = data.get("text", "").strip()  # may be empty if no arguments

        # Step 1: Post an initial thread message (this becomes the thread anchor)
        try:
            init_resp = slack_client.chat_postMessage(
                channel=channel_id,
                text="Thread started—please type your Trello command below."
            )
            # Capture the thread's ts for future replies.
            thread_ts = init_resp["ts"]
            print("Thread initiated with ts:", thread_ts)

        except Exception as e:
            print(f"Error creating thread: {e}")
            return jsonify({"text": "Error starting thread"}), 500

        # Optionally, if the user provided text with the slash command,
        # you can choose to process it as the first command within the thread.
        if user_text:
            threading.Thread(
                target=handle_message, 
                args=(data.get("user_id", ""), user_text, channel_id, thread_ts)
            ).start()

        # Immediately respond to the slash command with an ephemeral message.
        response_payload = {
            "response_type": "in_channel",
            "text": "Thread started—continue your commands in the thread below."
        }
        return jsonify(response_payload), 200

    return jsonify({"text": "No data received"}), 200

def handle_message(user_id, text, channel, thread_ts):
    # Update your OpenAI system prompt to include that context.
    system_prompt = (
        "You are ClutchAI, a Slack bot that assists with Trello board operations. "
        "This conversation is happening in a dedicated thread. Use the context of the thread "
        "and the latest Trello board data to determine the necessary Trello API actions and "
        "provide a natural language response. Here is the current board data: " + get_latest_board_data()
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
        # Ensure valid JSON output from OpenAI
        gpt_response = ast.literal_eval(response.choices[0].message.content)
    except Exception as e:
        send_slack_message(f"Error parsing AI response: {str(e)}", channel, thread_ts)
        return

    api_action = gpt_response.get('api_action')
    user_response = gpt_response.get('response')

    # Execute the Trello API action (if needed)
    if api_action:
        execute_trello_action(api_action)

    # Post the AI response in the thread
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
