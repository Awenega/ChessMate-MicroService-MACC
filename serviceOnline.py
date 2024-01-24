import chess
from flask_restful import Resource
from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter, Or
import json
from flask import jsonify, request, make_response
from helperCelery import listen_for_game_changes
from model.game import GameSchema, RoomData
from threading import Thread


def validate_token(request):
    token = request.headers.get('Authorization')
    return token == postgres_credentials.get('token')


def load_credentials():
    try:
        with open('credentials.json', 'r') as f:
            postgres_credentials = json.load(f)
            return postgres_credentials
    except FileNotFoundError:
        print("File containing credentials not found.")
        return None
    except json.JSONDecodeError:
        print("Error decoding JSON in credentials")
        return None


def create_room(data, room_ref, db):
    def generate_room_id(userId):
        import hashlib

        hashed_user_id = hashlib.sha256(userId.encode()).hexdigest()
        unique_string = f"room_{hashed_user_id}"
        return unique_string

    room_data = RoomData.from_dict(data)
    if not room_data:
        return make_response(jsonify({'msg': 'Invalid room data format.'}), 401)
    userId = data.get("playerOneId")
    roomId = generate_room_id(userId=userId)
    room_data.roomId = roomId
    room_ref.document(roomId).set(room_data.to_dict())
    #Thread(target=listen_for_game_changes, args=[roomId, db]).start()

    return make_response(room_data.to_dict(), 201)


def enter_room(roomId, data, room_ref, db):
    transaction = db.transaction()

    @firestore.transactional
    def try_enter(transaction, data):
        
        try:
            playerTwoId = data.get("playerOneId")
            playerTwoUsername = data.get("playerOneUsername")
            rankPlayerTwo = data.get("rankPlayerOne")
            room = room_ref.document(roomId)

            transaction.update(room, {
                "playerTwoId": playerTwoId,
                "playerTwoUsername": playerTwoUsername,
                "rankPlayerTwo": rankPlayerTwo,
                "gameState": "JOINED"
            })
            return True
        except Exception as e:
            print(f"Error: {e}")
            return False

    return try_enter(transaction=transaction, data=data)


# POSTGRES DB
postgres_credentials = load_credentials()
database = f"dbname={postgres_credentials.get('dbname')} user={postgres_credentials.get('user')} host={postgres_credentials.get('host')} password={postgres_credentials.get('password')}"


class OnlineResource(Resource):
    def __init__(self, db):
        self.db = db
        self.rooms_ref = db.collection("Rooms")

    def get(self, id):
        if not validate_token(request):
            return make_response(jsonify({'msg': 'Unauthorized. Invalid or missing token.'}), 401)

        filter_1 = FieldFilter("playerOneId", "==", id)
        filter_2 = FieldFilter("playerTwoId", "==", id)

        or_filter = Or(filters=[filter_1, filter_2])

        docs = (self.rooms_ref
                .where(filter=or_filter)
                .limit(1)
                .stream())

        for doc in docs:
            return make_response(doc.to_dict(), 200)

        return make_response(jsonify({'msg': 'Room not found.'}), 404)

    def post(self,id):
        if not validate_token(request):
            return make_response(jsonify({'msg': 'Unauthorized. Invalid or missing token.'}), 401)
    
        parameters = request.json
        try:
            if isinstance(parameters, dict):
                room_info = GameSchema().load(parameters)
            else:
                room_info = GameSchema().loads(parameters)
    
            rankPlayerTwo = float(room_info.get("rankPlayerOne"))
            docs = (self.rooms_ref
                    .where(filter=FieldFilter("gameState", "==", "CREATED"))
                    .where(filter=FieldFilter("rankPlayerOne", ">=", rankPlayerTwo - 300.0))
                    .where(filter=FieldFilter("rankPlayerOne", "<=", rankPlayerTwo + 300.0))
                    .limit(10)
                    .stream())
            print(parameters)
            for doc in docs:
                docDict = doc.to_dict()
                roomId = docDict.get("roomId")
                result = enter_room(
                    roomId=roomId, data=room_info, room_ref=self.rooms_ref, db=self.db)
                if result:
                    return make_response(jsonify({'roomId': roomId, **docDict}), 200)
            return create_room(data=room_info, room_ref=self.rooms_ref, db=self.db)

        except Exception as e:
            print("Post game error: ",str(e))
            return make_response(jsonify({'msg': str(e)}), 402)

