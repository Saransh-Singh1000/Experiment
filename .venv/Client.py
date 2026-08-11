

from ursina import *
from ursina.prefabs.first_person_controller import FirstPersonController
import threading, time, uuid, math
from random import uniform
import sounddevice as sd
import numpy as np
import base64
import json
from websocket import WebSocketApp as WSApp

# === CONFIG ===
SERVER_URL = 'wss://action-nutritional-motels-retailers.trycloudflare.com'
PLAYER_UPDATE_INTERVAL = 1.0
PLAYER_MODEL_PATH = 'Assets/Player.obj'
PLAYER_TEXTURE_PATH = 'Assets/Player.png'
GROUND_TEXTURE_PATH = 'Assets/Grass.png'
BOX_TEXTURE_PATH = 'Assets/SteelCountainer.png'
CHUNK_SIZE = 50
VIEW_RADIUS = 2

# === AUDIO CONFIG ===
AUDIO_CHUNK_SIZE = 4096
AUDIO_RATE = 44100
AUDIO_CHANNELS = 1
AUDIO_VOLUME = 4.0  # Amplify received audio by 4x

# === UNIQUE PLAYER ID ===
my_id = str(uuid.uuid4())
other_players = {}
voice_recording = False
voice_recording_thread = None
main_ws = None
audio_queue = []
vc_enabled = False
ws_connected = False

# === UTILITY: Get Model Height ===
def get_model_height(path):
    temp = Entity(model=path)
    height = temp.bounds.size.y
    destroy(temp)
    return height

# === VOICE CHAT: WEBSOCKET HANDLERS ===
def on_ws_open(ws):
    global ws_connected
    ws_connected = True
    print('✅ Connected to game server')
    ws.send(json.dumps({'type': 'connect', 'id': my_id}))

def on_ws_message(ws, message):
    try:
        data = json.loads(message)
        
        if data['type'] == 'voice':
            # Receive audio from other players
            audio_data = base64.b64decode(data['audio'])
            audio_queue.append(audio_data)
            print(f'🔊 Voice received from {data["from"][:8]}... ({len(audio_data)} bytes)')
        
        elif data['type'] == 'players':
            # Process player positions
            process_player_update(data['data'])
        
        elif data['type'] == 'chunk':
            # Process chunk containers
            process_chunk_update(data)
            
    except Exception as e:
        print(f'Receive error: {e}')

def on_ws_close(ws, close_status_code, close_msg):
    global ws_connected
    ws_connected = False
    print('❌ Disconnected from game server')

def on_ws_error(ws, error):
    print(f'Server error: {error}')

def init_server_connection():
    global main_ws
    try:
        server_url = SERVER_URL.strip()
        main_ws = WSApp(
            server_url,
            on_open=on_ws_open,
            on_message=on_ws_message,
            on_close=on_ws_close,
            on_error=on_ws_error
        )
        ws_thread = threading.Thread(target=main_ws.run_forever, daemon=False)
        ws_thread.start()
        time.sleep(1)  # Wait for connection to establish
    except Exception as e:
        print(f'Server connection error: {e}')

# === VOICE CHAT: RECORD AND SEND AUDIO ===
def record_and_send_voice():
    global voice_recording
    
    if not ws_connected or not main_ws:
        print('Server not connected')
        return
    
    print('🎙️ Recording... (hold V)')
    voice_recording = True
    
    try:
        with sd.InputStream(channels=AUDIO_CHANNELS, samplerate=AUDIO_RATE, 
                           blocksize=AUDIO_CHUNK_SIZE, dtype=np.float32) as stream:
            while voice_recording:
                audio_data, _ = stream.read(AUDIO_CHUNK_SIZE)
                
                # Convert float32 to bytes and send
                try:
                    audio_bytes = (audio_data * 32767).astype(np.int16).tobytes()
                    audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
                    main_ws.send(json.dumps({
                        'type': 'voice',
                        'audio': audio_b64
                    }))
                except Exception as e:
                    print(f'Send voice error: {e}')
                    break
    except Exception as e:
        print(f'Recording error: {e}')
    finally:
        print('🎙️ Recording stopped')

# === VOICE CHAT: PLAYBACK AUDIO FROM QUEUE ===
def playback_voice():
    try:
        with sd.OutputStream(channels=AUDIO_CHANNELS, samplerate=AUDIO_RATE, 
                            blocksize=AUDIO_CHUNK_SIZE, dtype=np.float32) as stream:
            while True:
                if audio_queue:
                    data = audio_queue.pop(0)
                    try:
                        # Convert bytes back to float32 and amplify
                        audio_float = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32767
                        audio_float = np.clip(audio_float * AUDIO_VOLUME, -1.0, 1.0)  # Amplify and clip
                        stream.write(audio_float.reshape(-1, 1))
                    except Exception as e:
                        print(f'Playback error: {e}')
                else:
                    time.sleep(0.01)
    except Exception as e:
        print(f'Playback setup error: {e}')

# === NETWORK: SEND POSITION VIA WEBSOCKET ===
def update_position():
    while True:
        try:
            if ws_connected and main_ws:
                main_ws.send(json.dumps({
                    'type': 'position',
                    'id': my_id,
                    'x': round(player.x, 2),
                    'y': round(player.y, 2),
                    'z': round(player.z, 2)
                }))
        except Exception as e:
            pass  # Silently fail if not connected
        time.sleep(PLAYER_UPDATE_INTERVAL)

# === NETWORK: FETCH REMOTE PLAYERS VIA WEBSOCKET ===
def process_player_update(players_data):
    global other_players
    
    cx, cz = chunk_key(player.x, player.z)
    active_ids = set()

    for pid, pos in players_data.items():
        if pid == my_id:
            continue

        px, pz = pos['x'], pos['z']
        pcx, pcz = chunk_key(px, pz)

        if abs(pcx - cx) <= VIEW_RADIUS and abs(pcz - cz) <= VIEW_RADIUS:
            adjusted_y = pos['y'] + scaled_height / 2 + 0.1
            active_ids.add(pid)

            if pid not in other_players:
                other_players[pid] = Entity(
                    model=PLAYER_MODEL_PATH,
                    texture=PLAYER_TEXTURE_PATH,
                    scale=player_scale,
                    position=(px, adjusted_y, pz),
                    collider='box',
                    double_sided=True
                )
            else:
                p = other_players[pid]
                p.x = px
                p.y = adjusted_y
                p.z = pz

    for pid in list(other_players.keys()):
        if pid not in active_ids:
            destroy(other_players[pid])
            del other_players[pid]

def fetch_players():
    while True:
        try:
            if ws_connected and main_ws:
                main_ws.send(json.dumps({'type': 'get_players'}))
        except Exception as e:
            pass  # Silently fail if not connected
        time.sleep(PLAYER_UPDATE_INTERVAL)

# === NETWORK: FETCH CHUNK CONTAINERS VIA WEBSOCKET ===
def process_chunk_update(data):
    global chunk_boxes, pending_chunks
    
    cx, cz = data['cx'], data['cz']
    pending_chunks.discard((cx, cz))
    containers = data['containers']
    
    print(f'📦 Chunk ({cx}, {cz}): {len(containers)} containers received')
    
    boxes = []
    for container in containers:
        box = Entity(
            model='cube',
            texture=BOX_TEXTURE_PATH,
            scale=(container['scale']['x'], container['scale']['y'], container['scale']['z']),
            position=(container['x'], container['y'], container['z']),
            collider='box',
            color=color.white
        )
        box.surface_type = 'metal'
        boxes.append(box)
    
    chunk_boxes[(cx, cz)] = boxes

def fetch_chunk_containers(cx, cz):
    try:
        if ws_connected and main_ws:
            print(f'📡 Requesting chunk containers ({cx}, {cz})')
            main_ws.send(json.dumps({
                'type': 'get_chunk',
                'cx': cx,
                'cz': cz
            }))
    except Exception as e:
        print(f'Chunk request error: {e}')

# === URSINA GAME ===
app = Ursina()

# Calculate model height and scale
raw_model_height = get_model_height(PLAYER_MODEL_PATH)
player_scale = 1.0
scaled_height = raw_model_height * player_scale

# Local player controller
player = FirstPersonController(
    position=(0, scaled_height / 2 + 0.1, 0),
    collider='box',
)
player.gravity = 1
player.height = scaled_height
player.speed = 12

# Attach visual model manually
player_model = Entity(
    parent=player,
    model=PLAYER_MODEL_PATH,
    texture=PLAYER_TEXTURE_PATH,
    scale=player_scale,
    position=(0, 0, 0),
    double_sided=True
)

# Camera setup using scaled height
camera.parent = player
camera.position = (0, scaled_height + 0.2, 0)
camera.rotation = (0, 0, 0)

# ESC toggles mouse lock
mouse_locked = True
mouse.locked = True

# === VC STATUS BAR ===
vc_status_text = Text(
    text='🎙️ VC: DISABLED',
    origin=(0, 0),
    position=(-0.45, 0.46),
    scale=1.5,
    color=color.red
)

def input(key):
    global mouse_locked
    if key == 'escape':
        mouse_locked = not mouse_locked
        mouse.locked = mouse_locked
        mouse.visible = not mouse_locked

# === CHUNK SYSTEM ===
active_chunks = {}
chunk_boxes = {}
pending_chunks = set()

def chunk_key(x, z):
    return (math.floor(x / CHUNK_SIZE), math.floor(z / CHUNK_SIZE))

# === SPAWN CONTAINERS FROM SERVER DATA (VIA WEBSOCKET) ===
def create_chunk(cx, cz):
    if (cx, cz) in pending_chunks:
        return None  # Already requested, wait for response
    
    print(f'🗺️ Creating chunk ({cx}, {cz})')
    world_x = cx * CHUNK_SIZE
    world_z = cz * CHUNK_SIZE
    chunk = Entity(
        model='plane',
        scale=(CHUNK_SIZE, 1, CHUNK_SIZE),
        position=(world_x + CHUNK_SIZE / 2, -0.5, world_z + CHUNK_SIZE / 2),
        texture=GROUND_TEXTURE_PATH,
        texture_scale=(CHUNK_SIZE, CHUNK_SIZE),
        collider='box',
        color=color.green
    )
    chunk.surface_type = 'grass'  # Mark ground surface type
    
    # Request containers from server
    pending_chunks.add((cx, cz))
    fetch_chunk_containers(cx, cz)
    
    # Initialize empty box list, will be populated when server responds
    chunk_boxes[(cx, cz)] = []
    
    return chunk

def update_chunks():
    cx, cz = chunk_key(player.x, player.z)
    for dx in range(-VIEW_RADIUS, VIEW_RADIUS + 1):
        for dz in range(-VIEW_RADIUS, VIEW_RADIUS + 1):
            key = (cx + dx, cz + dz)
            if key not in active_chunks:
                chunk = create_chunk(*key)
                if chunk is not None:  # Only add if chunk was created successfully
                    active_chunks[key] = chunk

    to_remove = []
    for key in list(active_chunks.keys()):
        if abs(key[0] - cx) > VIEW_RADIUS + 1 or abs(key[1] - cz) > VIEW_RADIUS + 1:
            to_remove.append(key)

    for key in to_remove:
        if active_chunks[key] is not None:  # Check before destroying
            destroy(active_chunks[key])
        del active_chunks[key]

        # Unload boxes
        if key in chunk_boxes:
            for box in chunk_boxes[key]:
                if box is not None:
                    destroy(box)
            del chunk_boxes[key]

# === SOUND SETUP ===
grass_sound = Audio('Assets/WalkingOnGrass.mp3', loop=True, autoplay=False, volume=1)
metal_sound = Audio('Assets/WalkingOnMetal.mp3', loop=True, autoplay=False, volume=1)
current_surface = None

# === VERTICAL CAMERA MOVEMENT + CHUNK UPDATE + SURFACE SOUND + VOICE CHAT ===
def update():
    global current_surface, voice_recording, voice_recording_thread, vc_enabled

    camera.rotation_x -= mouse.velocity[1] * 40
    camera.rotation_x = clamp(camera.rotation_x, -90, 90)
    update_chunks()
    
    # === VOICE CHAT: HOLD V TO TALK ===
    v_pressed = held_keys['v']
    
    if v_pressed and not voice_recording:
        # Start recording
        voice_recording = True
        vc_enabled = True
        voice_recording_thread = threading.Thread(target=record_and_send_voice, daemon=True)
        voice_recording_thread.start()
        vc_status_text.text = '🎙️ VC: ENABLED'
        vc_status_text.color = color.lime
    elif not v_pressed and voice_recording:
        # Stop recording
        voice_recording = False
        vc_enabled = False
        vc_status_text.text = '🎙️ VC: DISABLED'
        vc_status_text.color = color.red

    # Detect surface type under player using surface_type attribute
    hit_info = raycast(player.world_position + Vec3(0, 0.5, 0), Vec3(0, -1, 0), distance=2, ignore=(player,))
    surface_type = None
    if hit_info.hit and hasattr(hit_info.entity, 'surface_type'):
        surface_type = hit_info.entity.surface_type

    # Determine if moving on ground
    is_moving = (held_keys['w'] or held_keys['a'] or held_keys['s'] or held_keys['d']) and player.grounded
    if is_moving:
        if surface_type != current_surface:
            # Stop old sound
            if grass_sound.playing:
                grass_sound.stop()
            if metal_sound.playing:
                metal_sound.stop()

            # Play new sound
            if surface_type == 'grass':
                grass_sound.play()
            elif surface_type == 'metal':
                metal_sound.play()

            current_surface = surface_type
        else:
            # If same surface, but sound not playing, play again
            if surface_type == 'grass' and not grass_sound.playing:
                grass_sound.play()
            elif surface_type == 'metal' and not metal_sound.playing:
                metal_sound.play()
    else:
        # Stop all sounds if not moving
        if grass_sound.playing:
            grass_sound.stop()
        if metal_sound.playing:
            metal_sound.stop()
        current_surface = None

Sky()
update_chunks()

# === INITIALIZE UNIFIED WEBSOCKET CONNECTION ===
init_server_connection()

threading.Thread(target=update_position, daemon=True).start()
threading.Thread(target=fetch_players, daemon=True).start()
threading.Thread(target=playback_voice, daemon=True).start()

app.run()
