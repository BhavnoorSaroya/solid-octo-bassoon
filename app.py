from flask import Flask, request, jsonify, Response
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

# Store user conversations
user_sessions = {}
conversation_sessions = {}

# Slack event endpoint
@app.route("/slack/events", methods=["POST"])
def slack_events():
    data = request.form
    
    
    user_id = data.get("user_id")
    text = data.get("text")
    channel = data.get("channel_id")
    response_url = data.get("response_url")  # Use this for ephemeral responses

    # print("data", data)

    
    
    # print(request.json)
    # # Handle Slack's challenge request
    # if "challenge" in request.json:
    #     data = request.json
    #     print("Challenge request received")
    #     return jsonify({"challenge": data["challenge"]})
    print("hello ")
    print("user_id", user_id)
    
    
    print(type(text))

    if not user_id:
        data = request.json
        event = data["event"]
        if event["user"] == "U08A9L74XGU":
            return Response(status=200) # Ignore messages from the bot itself
        
        print("aage")
        text = event["text"]
        channel = event["channel"]
        thread_ts = event["thread_ts"] or event["ts"]  # Check if it's a reply
        # print("YES OR NO", thread_ts)
        if thread_ts:
            print(event["user"])
            # response = slack_client.chat_postMessage( # test
            #     channel=event["channel"], 
            #     text=f"cooking",
            #     thread_ts=thread_ts
            # )
            threading.Thread(target=handle_message, args=(user_id, text, channel, thread_ts)).start()
            return jsonify("TEST"), 200

        print("not a slash command")
        return Response(status=200)

        # return jsonify({"status": "ignored"}), 200
        
    print("user_id", user_id)


    # Post an initial message and capture ts
    # response = slack_client.chat_postMessage(
    #     channel=channel, 
    #     text=f"Processing: {text}...",
    # )
    thread_ts = data.get("ts") or data.get("thread_ts")  # Capture timestamp
    print("thread_ts", thread_ts)
    

    # Store the conversation history
    if user_id not in user_sessions:
        user_sessions[user_id] = {"messages": []}
        
        
    

    user_sessions[user_id]["messages"].append({"role": "user", "content": text})
    
    
    
    
        # Store message in the existing thread session if it exists
    if thread_ts in conversation_sessions:
        conversation_sessions[thread_ts]["messages"].append({"role": "user", "content": text})
        threading.Thread(target=handle_message, args=(user_id, text, channel, thread_ts)).start()
        return jsonify({"status": "Processing thread reply"}), 200

    # Process the message in a separate thread
    threading.Thread(target=handle_message, args=(user_id, text, channel, thread_ts)).start()

    # return jsonify({"status": "Processing"}), 200
    return Response(status=200)

def handle_message(user_id, text, channel, thread_ts):
    """Process user input and continue conversation until action is ready."""
    
    # If this is a new conversation, initialize it
    if thread_ts not in conversation_sessions:
        conversation_sessions[thread_ts] = {"messages": []}

    # Append user's message to conversation history
    conversation_sessions[thread_ts]["messages"].append({"role": "user", "content": text})

    # Step 1: Send conversation to GPT
    response = openai.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system",
                "content": 
                """You are a bot that processes Trello requests and generates:
                    1. The Trello API action needed (endpoint, method, parameters).
                    2. A natural language response for the user.
                    3. If more details are needed, return {"response": "your question here"}.

                    ### **Response Format Guidelines**
                    - Always return **valid JSON**.
                    - If **enough details** are provided, include `api_action` and `response`.
                    - If **more details are needed**, return **only** a `response` field with a question.

                    ---
                    ### **Valid Examples:**
                    #### **Example 1: When all details are provided**
                    **User:** "Create a card in the 'To Do' list titled 'Fix bug #123'."
                    **Bot Response:**
                    ```json
                    {
                        "api_action": {
                            "endpoint": "/cards?idList=67a104b5f5273bdd9291d310",
                            "method": "POST",
                            "parameters": {
                                "name": "Fix bug #123"
                            }
                        },
                        "response": "I created a card titled 'Fix bug #123' in the 'To Do' list."
                    }
                    
                    
                    Example 2: When more details are needed

                    User: "Create a card in the 'To Do' list." Bot Response:

                    {
                        "response": "What should the card be titled?"
                    }
                    
                    Example 3: When user asks to delete a card

                    User: "Delete the card 'Bug Fix #123' from the 'To Do' list." Bot Response:

                    {
                        "api_action": {
                            "endpoint": "/cards/{card_id}",
                            "method": "DELETE",
                        },
                        "response": "I deleted the card 'Bug Fix #123' from the 'To Do' list."
                    }

                     Important Rules
                        Ensure responses are always valid JSON.
                        
                """ + f"Here is the latest trello board data: {get_latest_board_data()}."
            },
            *conversation_sessions[thread_ts]["messages"]  # Full history for this thread
        ]
    )

    # Step 2: Parse GPT response
    try:
        gpt_response = ast.literal_eval(response.choices[0].message.content)
    except (SyntaxError, ValueError):
        send_slack_message("Sorry, I couldn't understand that. Try again!", channel, thread_ts)
        return
    
    # Step 3: Check if GPT is requesting more info
    if "response" in gpt_response and "api_action" not in gpt_response:
        follow_up_question = gpt_response["response"]

        # Append bot response to conversation history
        conversation_sessions[thread_ts]["messages"].append({"role": "assistant", "content": follow_up_question})

        # Continue conversation in the same thread
        send_slack_message(follow_up_question, channel, thread_ts)
        return  # Do not execute Trello action yet

    # Step 4: If we have an API action, execute Trello request
    if "api_action" in gpt_response:
        api_action = gpt_response["api_action"]
        user_response = gpt_response["response"]

        execute_trello_action(api_action)

        send_slack_message(user_response, channel, thread_ts)

        # Clear session after task completion
        del conversation_sessions[thread_ts]




def execute_trello_action(action):
    print("Executing Trello Action:", action)
    endpoint = action['endpoint']
    method = action['method']
    parameters = action.get('parameters', {})
    parameters.update({"key": TRELLO_API_KEY, "token": TRELLO_API_TOKEN})
    url = f"{TRELLO_API_BASE}{endpoint}?key={TRELLO_API_KEY}&token={TRELLO_API_TOKEN}"
    print("URL:", url)
    if method == "POST":
        res = requests.post(url, json=parameters)
    elif method == "PUT":
        res = requests.put(url, json=parameters)
    elif method == "DELETE":
        res = requests.delete(url, params=parameters)
    
    print(f"Trello API Response: {res.status_code}")

def send_slack_message(text, channel, thread_ts):
    print("Sending message:", text)
    slack_client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)


def get_latest_board_data():
    board_id = TRELLO_BOARD_ID
    print("getting board data")
    url = f"{TRELLO_API_BASE}/boards/{board_id}?key={TRELLO_API_KEY}&token={TRELLO_API_TOKEN}"
    
    response = requests.get(url)
    boards = response.json()

# Health check endpoint
@app.route("/health", methods=["GET"])
def health_check():
    answer = 1 + 1
    print(answer)
    if answer == 2:
        print("health check, universe in order")
    
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(port=5000, host="0.0.0.0", debug=True)
