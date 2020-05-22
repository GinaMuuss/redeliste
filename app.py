#!/usr/bin/env python
from threading import Lock
from flask import Flask, render_template, session, request, copy_current_request_context, make_response, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room, close_room, rooms, disconnect, Namespace
import uuid

async_mode = None

app = Flask(__name__)

secret = uuid.uuid4()
print("flask secret key is : ", str(secret))
app.config['SECRET_KEY'] = str(secret)
socketio = SocketIO(app, async_mode=async_mode)
thread = None
thread_lock = Lock()
rooms = {}
users = {}

class User():
    def __init__(self, name):
        self.name = name.strip()
        self.user_id = uuid.uuid4() # This identifyies the user
        self.unique_id = uuid.uuid4() # This is the key only the user is supposed to have

    def get_id(self):
        return str(self.user_id)

    def to_json(self):
        return {"name": self.name, "user_id": self.user_id, "unique_id": self.unique_id}

    @classmethod
    def from_json(cls, value):
        u = cls("")
        u.name = value["name"].strip()
        u.user_id = value["user_id"]
        u.unique_id = value["unique_id"]
        return u


class HandList():
    def __init__(self, name):
        self.name = name
        self.current_list = []
        self.user_names = {}
        self.is_frozen = False
        self.channel_id = uuid.uuid4()

    def add_hand(self, user):
        if users[user.get_id()].unique_id != user.unique_id:
            return False
        if self.is_frozen or user.get_id() in self.current_list:
            return False
        self.user_names[user.get_id()] = user.name
        self.current_list.append(user.get_id())
        return True

    def remove_hand(self, user):
        if users[user.get_id()].unique_id != user.unique_id:
            return False
        if user.get_id() in self.current_list:
            self.current_list.remove(user.get_id())
            del self.user_names[user.get_id()]
            return True
        return False

    def to_json(self):
        return {"name": self.name, "current_list": [self.user_names[x] for x in self.current_list], "current_id_list": self.current_list, "is_frozen": self.is_frozen, "channel_id": str(self.channel_id)}


class AdminRoom(Namespace):
    def __init__(self, room):
        """
            Creates a new admin room, used to send updates to the admins
        """
        self.key = uuid.uuid4()
        self.room = room
        Namespace.__init__(self, "/" + str(self.key))
        socketio.on_namespace(self)

    def broadcast_to_admin(self, data):
        socketio.emit("data_update", data, namespace=self.namespace)

    def on_connect(self):
        print("admin connected")
        self.room.trigger_update_admin()

    def on_remove_raise(self, data):
        user = User("")
        user.id = uuid.UUID(data["user_id"])
        print("session force lower", user.id)
        self.room.current_hands[uuid.UUID(data["channel_id"])].remove_hand(user)
        self.room.trigger_update_admin()
        self.room.trigger_update_guest()


class Room(Namespace):
    def __init__(self, name, hands):
        """
            Creates a new room, name is a string and hands is a list of notifications to allow
            The Namespace is the guest namespace. 
        """
        global rooms
        self.name = name
        self.guest_key = uuid.uuid4()
        self.admin_room = AdminRoom(self)
        self.current_hands = {}
        for hand in hands:
            h = HandList(hand)
            self.current_hands[h.channel_id] = h
        rooms[self.guest_key] = self
        Namespace.__init__(self, "/" + str(self.guest_key))
        socketio.on_namespace(self)

    def get_channels(self):
        return [{"name": self.current_hands[x].name, "id": self.current_hands[x].channel_id} for x in self.current_hands]

    def on_raise_hand_event(self, data):
        user = User.from_json(session["user"])
        print("session raise", user.name)
        self.current_hands[uuid.UUID(data["channel_id"])].add_hand(user)
        self.trigger_update_admin()
        self.trigger_update_guest()

    def on_lower_hand_event(self, data):
        user = User.from_json(session["user"])
        print("session lower", user.name)
        self.current_hands[uuid.UUID(data["channel_id"])].remove_hand(user)
        self.trigger_update_admin()
        self.trigger_update_guest()

    def trigger_update_admin(self):
        self.admin_room.broadcast_to_admin([self.current_hands[x].to_json() for x in self.current_hands])

    def trigger_update_guest(self):
         self.emit("data_update", [self.current_hands[x].to_json() for x in self.current_hands], namespace=self.namespace)

    def on_connect(self):
        print("con")
        self.trigger_update_guest()


    def on_disconnect(self):
        print("discon")



@app.route('/')
def index():
    if "user" in session:
        return render_template('index.html.j2')
    else:
        return render_template('guest_login.html.j2', room_id="index_placeholder")


@app.route('/guest/<room_id>')
def guest_room_id(room_id):
    if "user" in session:
        # user is registered
        uu = uuid.UUID(room_id)
        if uu in rooms:
            return render_template("admin.html.j2", room=rooms[uu], admin=False)
        else:
            return "Error, room not found", 404
    else:
        # user is new
        return render_template('guest_login.html.j2', room_id=room_id)


@app.route('/guest', methods=['GET', 'POST'])
def guest():
    if request.method == 'POST':
        if request.form['name'] == '':
            return render_template('guest_login.html.j2')
        user = User(request.form['name'])
        session['user'] = user.to_json()
        users[user.get_id()] = user

        if request.form['room_id'] == "index_placeholder":
            return redirect(url_for('index'))
        if uuid.UUID(request.form['room_id']) in rooms:
            return redirect(url_for('guest_room_id', room_id=request.form['room_id']))
    return render_template('guest_login.html.j2')


@app.route('/admin/<room_id>')
def admin(room_id):
    # user is registered
    uu = uuid.UUID(room_id)
    room = None
    for x in rooms:
        if rooms[x].admin_room.key == uu:
            room = rooms[x]
            break
    if room:
        return render_template('admin.html.j2', room=room, admin=True)
    else:
        return "Error, room not found", 404


@app.route("/admin_generate", methods=['POST'])
def admin_generate():
    room = Room(request.form['room_name'], request.form['channels'].split(","))
    return redirect(url_for('admin', room_id=room.admin_room.key))
    


if __name__ == '__main__':
    socketio.run(app, debug=False, host='0.0.0.0')
