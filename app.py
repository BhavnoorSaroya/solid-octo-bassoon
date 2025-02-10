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

# Slack setup
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
signature_verifier = SignatureVerifier(os.getenv("SLACK_SIGNING_SECRET"))
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL")

# OpenAI setup
openai.api_key = os.getenv("OPENAI_API_KEY")

# Trello setup
TRELLO_API_KEY = os.getenv("TRELLO_API_KEY")
TRELLO_API_TOKEN = os.getenv("TRELLO_API_TOKEN")
TRELLO_API_BASE = "https://api.trello.com/1"
TRELLO_BOARD_ID = os.getenv("TRELLO_BOARD_ID")

# Slack event endpoint
@app.route("/slack/events", methods=["POST", "GET"])
def slack_events():
    # Verify request signature
    # if not signature_verifier.is_valid_request(request.get_data(), request.headers):
    #     return "Invalid request signature", 403

    # Parse event data
    data = request.form


    if data and "channel_id" in data:
        channel_id = data["channel_id"]
        user_text = data.get("text", "")  # the text following /clutchAI
        
        # Step 1: Post an initial message to start the thread.
        try:
            # This message will act as the thread “parent.”
            init_resp = slack_client.chat_postMessage(
                channel=channel_id,
                text="Thread started—processing your request..."
            )
            thread_ts = init_resp["ts"]
        except Exception as e:
            print(f"Error creating thread: {e}")
            return jsonify({"text": "Error starting thread"}), 500

        # Start your processing in a new thread (pass along the channel and thread_ts)
        threading.Thread(
            target=handle_message, 
            args=(data.get("user_id", ""), user_text, channel_id, thread_ts)
        ).start()

        # Immediately respond to the slash command so Slack doesn’t show an error.
        # This initial response appears in the channel.
        response_payload = {
            "response_type": "in_channel",
            "text": "Processing your request in a thread..."
        }
        return jsonify(response_payload), 200

    return jsonify({"text": "No data received"}), 200



def handle_message(user_id, text, channel, thread_ts):
    # Step 1: Send the user's message to OpenAI
    response = openai.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system",
                "content": """You are a bot that processes Trello requests and generates:
                1. The Trello API action needed (endpoint, method, parameters).
                2. A natural language response for the user.

                User Request: "Create a card in the 'To Do' list of the 'Project Alpha' board titled 'Fix bug #123'."

                Respond in this format:

                {
                "api_action": {
                    "endpoint": "/cards/", #an example might be /1/boards/?=boardname
                    "method": "POST", #POST, GET, PUT, DELETE
                    "url_params": "?idList=67a104b5f5273bdd9291d310"
                    "parameters": {
                    "name": "Fix bug #123",
                    "idList": "LIST_ID" #the ID of the list
                    }
                },
                "response": "I went ahead created a card titled 'Fix bug #123' in the 'To Do' list"
                }
                Make sure the JSON is valid, and always include both the `api_action` and `response` fields. use board id 67a104b5f5273bdd9291d2a9, 
 """ + f"here is the latest card and list data: {get_latest_board_data()}"},
            {"role": "user", "content": text}
        ]
    )

    # choose an appropriate list from: todo: 67a104b5f5273bdd9291d310, in progress: 6613945fa358664a00f38d56, further along: 66139491e4b737bc8da66f92, and complete: 66139465939def4c4e167040
    
    
    # print(response.choices[0].message.content)
    
    gpt_response = ast.literal_eval(response.choices[0].message.content)
    
    print("api actions", gpt_response['api_action'])
    # response_data = eval(gpt_response)  # Convert response to dict (ensure OpenAI returns valid JSON)
    api_action = gpt_response['api_action']
    user_response = gpt_response['response']

    # Step 2: Execute the Trello API action
    execute_trello_action(api_action)

    # Step 3: Respond back to Slack
    send_slack_message(user_response, channel, thread_ts)

    # except Exception as e:
        # slack_client.chat_postMessage(channel=channel, text=f"Error processing your request: {str(e)}")
    # return "success from handle_message"

def execute_trello_action(action):
    print("executing action")
    endpoint = action['endpoint']
    method = action['method']
    parameters = action['parameters']
    try:
        url_params = action['url_params']
    except KeyError:
        url_params = "?"

    print(parameters)

    # Add authentication to parameters
    parameters.update({
        "key": TRELLO_API_KEY,
        "token": TRELLO_API_TOKEN
    })

    # Make the API call
    url = f"{TRELLO_API_BASE}{endpoint}{url_params}&key={TRELLO_API_KEY}&token={TRELLO_API_TOKEN}"
    print(url)
    if method == "POST":
        res = requests.post(url, json=parameters)
    elif method == "PUT":
        res = requests.put(url, json=parameters)
    elif method == "DELETE":
        res = requests.delete(url, params=parameters)
    print("Trello action status:", res.status_code)
        


def get_latest_board_data():
    board_id = TRELLO_BOARD_ID
    print("getting board data")
    url = f"{TRELLO_API_BASE}/boards/{board_id}/cards?key={TRELLO_API_KEY}&token={TRELLO_API_TOKEN}"
    
    response = requests.get(url)
    boards = response.json()
    
    
    
    url = f"{TRELLO_API_BASE}/boards/{board_id}/lists?key={TRELLO_API_KEY}&token={TRELLO_API_TOKEN}"
    response = requests.get(url)
    lists = response.json()
    
    data = {"cards": boards, "lists": lists}
    
    print("returning board data", str(data))
    return str(data)


def send_slack_message(text, channel, thread_ts=None):
    try:
        response = slack_client.chat_postMessage(
            channel=channel,
            text=text,
            thread_ts=thread_ts  # If thread_ts is provided, the message is posted as a reply in that thread.
        )
        print("Message sent with ts:", response["ts"])
    except Exception as e:
        print("Error sending message:", e)


# Health check endpoint
@app.route("/health", methods=["GET"])
def health_check():
    print("Health check OK")
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(port=5000, host="0.0.0.0", debug=True)