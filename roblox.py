from urllib3 import PoolManager
import urllib3
from urllib.parse import urlsplit, quote
from input import press_key, release_key, bulk_press_and_release_key, send_key_to_window
from threading import Lock
from PIL import Image, ImageGrab
import json
import time
import subprocess
import random
import ctypes
import os
import win32gui
import win32process
import win32gui
import win32com.client

                                                             
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


                             
ACTION_MOVE = 'move'
ACTION_JUMP = 'jump'
ACTION_CAMERA = 'camera'
ACTION_IDLE = 'idle'                        

                      
MOVE_KEYS = [0x57, 0x41, 0x53, 0x44]              

                  
CAMERA_KEYS = [0x25, 0x26, 0x27, 0x28]                                     

          
JUMP_KEY = 0x20            

                                                              
ACTION_WEIGHTS = {
    ACTION_MOVE: 50,                              
    ACTION_JUMP: 20,                
    ACTION_CAMERA: 20,              
    ACTION_IDLE: 10,                                       
}

                      
randkeys = MOVE_KEYS

shell = win32com.client.Dispatch("WScript.Shell")
client_lock = Lock()

def get_hwnds_for_pid(pid):
    def callback(hwnd, hwnds):
        if win32gui.IsWindowVisible(hwnd) and win32gui.IsWindowEnabled(hwnd):
            _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
            if found_pid == pid:
                hwnds.append(hwnd)
        return True
        
    hwnds = []
    win32gui.EnumWindows(callback, hwnds)
    return hwnds

def find_client_path():
    base_paths = [
        os.path.join(os.environ["LOCALAPPDATA"], "Bloxstrap", "Versions"),             
        os.path.join(os.environ["LOCALAPPDATA"], "Roblox", "Versions"),
        "C:\\Program Files (x86)\\Roblox\\Versions",
        "C:\\Program Files\\Roblox\\Versions",
    ]
    
    for base_path in base_paths:
        if os.path.exists(base_path):
                                                                         
            for version_folder in os.listdir(base_path):
                version_path = os.path.join(base_path, version_folder)
                exe_path = os.path.join(version_path, "RobloxPlayerBeta.exe")
                if os.path.isfile(exe_path):
                    return version_path
    
    raise FileNotFoundError("Could not find path to Roblox client. Is Roblox installed?")

class RobloxClientMutex:
    def __init__(self):
        self.mutex = None
        enable_single_instance = os.getenv("ROBLOX_SINGLE_INSTANCE", "").strip().lower() in {"1", "true", "yes"}
        if enable_single_instance:
            self.mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "ROBLOX_singletonMutex")

                                                             
                                      
claimed_pids = set()
claimed_pids_lock = Lock()
CLAIMED_PIDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.claimed_pids.json')
CLAIMED_PID_TTL = 3600                                

def _is_pid_alive(pid):
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return exit_code.value == STILL_ACTIVE
        return False
    except Exception:
        return False

def _load_claimed_pids():
    global claimed_pids
    try:
        if os.path.exists(CLAIMED_PIDS_FILE):
            with open(CLAIMED_PIDS_FILE, 'r') as f:
                data = json.load(f)
            current_time = time.time()
            valid_pids = set()
            for pid_str, timestamp in data.items():
                pid = int(pid_str)
                                                
                if current_time - timestamp < CLAIMED_PID_TTL and _is_pid_alive(pid):
                    valid_pids.add(pid)
            claimed_pids = valid_pids
                                       
            _save_claimed_pids()
    except Exception:
        claimed_pids = set()

def _save_claimed_pids():
    try:
        current_time = time.time()
        data = {str(pid): current_time for pid in claimed_pids}
        with open(CLAIMED_PIDS_FILE, 'w') as f:
            json.dump(data, f)
    except Exception:
        pass

def claim_pid(pid):
    with claimed_pids_lock:
        claimed_pids.add(pid)
        _save_claimed_pids()

def release_pid(pid):
    with claimed_pids_lock:
        claimed_pids.discard(pid)
        _save_claimed_pids()

                                      
_load_claimed_pids()

class Client:
    redeem_url = "https://www.roblox.com/Login/Negotiate.ashx"

    def __init__(self, parent, place_id, job_id=None, launch_data=None):
        self.parent = parent
        self.place_id = place_id
        self.job_id = job_id
        self.launch_data = launch_data                                            
        self.process = None
        self.hwnd = None
        self.start()
    
    def __repr__(self):
        return f"Client for {self.parent}"

    def build_joinscript_url(self):
        base_url = "https://assetgame.roblox.com/game/PlaceLauncher.ashx"
        
        if self.place_id and self.job_id:
            script_url = f"{base_url}?request=RequestGameJob&browserTrackerId={self.parent.browser_tracker_id}&placeId={self.place_id}&gameId={self.job_id}&isPlayTogetherGame=false"
        elif self.place_id:
            script_url = f"{base_url}?request=RequestGame&browserTrackerId={self.parent.browser_tracker_id}&placeId={self.place_id}&isPlayTogetherGame=false"
        else:
            raise ValueError("place_id is required")
        
                                             
        if self.launch_data:
            encoded_data = quote(self.launch_data, safe='')
            script_url += f"&launchData={encoded_data}"
        
        return script_url

    def is_in_game(self, match_job_id=False):
        try:
            resp = self.parent.request(
                method="POST",
                url="https://presence.roblox.com/v1/presence/users",
                data={"userIds": [self.parent.id]}
            )
            data = resp.json()
            if not data.get("userPresences"):
                return True                               
            me = data["userPresences"][0]
                                                        
            current_place = me.get("placeId")
            target_place = int(self.place_id) if self.place_id else None
            if current_place is None:
                return False                   
            return current_place == target_place
        except Exception:
            return True                                                      

    def wait_for(self, timeout=10, match_job_id=False):
        st = time.time()
        while ((time.time()-st) < timeout):
            if self.is_in_game(match_job_id):
                return True
            time.sleep(1)
        raise TimeoutError

    def start(self):
        if self.process:
            raise Exception(".start() has already been called")

        auth_ticket = self.parent.request("POST", "https://auth.roblox.com/v1/authentication-ticket")\
            .headers["rbx-authentication-ticket"]
        
        launch_time = int(time.time()*1000)
        
                                                              
        joinscript_url = self.build_joinscript_url()
        encoded_joinscript = quote(joinscript_url, safe='')
        
                                                  
        deep_link = (
            f"roblox-player:1+launchmode:play"
            f"+gameinfo:{auth_ticket}"
            f"+launchtime:{launch_time}"
            f"+placelauncherurl:{encoded_joinscript}"
            f"+browsertrackerid:{self.parent.browser_tracker_id}"
            f"+robloxLocale:en_us"
            f"+gameLocale:en_us"
        )
        
                                     
        self.process = subprocess.Popen(
            ["cmd", "/c", "start", "", deep_link],
            shell=False
        )

        start_time = time.time()
        while (time.time()-start_time) < 30:                                         
                                                              
            def find_roblox_windows(hwnd, windows):
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    if "Roblox" in title:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        windows.append((hwnd, pid))
                return True
            
            windows = []
            win32gui.EnumWindows(find_roblox_windows, windows)
            
                                      
            with claimed_pids_lock:
                for hwnd, pid in windows:
                    if pid not in claimed_pids:
                                                       
                        self.hwnd = hwnd
                        self._roblox_pid = pid
                        break
            
                                                                        
            if self.hwnd and hasattr(self, '_roblox_pid'):
                claim_pid(self._roblox_pid)
            
            if self.hwnd:
                break
            time.sleep(0.5)
        
        if not self.hwnd:
            raise TimeoutError("Timed out while getting Roblox window")

    def close(self):
                                                                          
        if hasattr(self, '_roblox_pid'):
            release_pid(self._roblox_pid)
            try:
                import os
                os.kill(self._roblox_pid, 9)
                                                    
                for _ in range(10):
                    if not _is_pid_alive(self._roblox_pid):
                        break
                    time.sleep(0.1)
                else:
                    print(f"[WARNING] Process {self._roblox_pid} did not terminate")
            except Exception as e:
                print(f"[WARNING] Kill failed for PID {self._roblox_pid}: {e}")
        elif self.process:
            self.process.kill()

    def minimize(self):
        try:
            win32gui.ShowWindow(self.hwnd, 6)               
        except Exception:
            pass

    def set_low_priority(self):
        try:
            if hasattr(self, '_roblox_pid'):
                import ctypes
                PROCESS_SET_INFORMATION = 0x0200
                BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, self._roblox_pid)
                if handle:
                    ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
                    ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass

    def is_window_valid(self):
        try:
                                 
            if not win32gui.IsWindow(self.hwnd):
                return False
                                                  
            if hasattr(self, '_roblox_pid'):
                import ctypes
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                STILL_ACTIVE = 259
                handle = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, self._roblox_pid
                )
                if handle:
                    exit_code = ctypes.c_ulong()
                    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return exit_code.value == STILL_ACTIVE
            return True
        except Exception:
            return True                         

    def check_in_game(self, strict=False):
        try:
            result = self.is_in_game(match_job_id=False)
            return result
        except Exception:
            return None if strict else True

    def focus(self):
        try:
                            
            current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
            target_thread = ctypes.windll.user32.GetWindowThreadProcessId(self.hwnd, None)
            
                                                   
            ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, True)
            
            try:
                                                     
                ctypes.windll.user32.BringWindowToTop(self.hwnd)
                ctypes.windll.user32.SetForegroundWindow(self.hwnd)
                time.sleep(0.1)
            finally:
                                    
                ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, False)
        except Exception:
            pass

    def screenshot(self):
        with client_lock:
            self.focus()
            press_key(0x7A)
            release_key(0x7A)
            time.sleep(0.3)
            image = ImageGrab.grab()
            press_key(0x7A)
            release_key(0x7A)
        return image

    def chat_message(self, message):
        with client_lock:
            self.focus()
            press_key(0xBF)
            time.sleep(0.03)
            release_key(0xBF)
            time.sleep(0.05)
            bulk_press_and_release_key(message)
            press_key(0x0D)
            time.sleep(0.03)
            release_key(0x0D)
            time.sleep(0.08)
    def antiafk(self):
        with client_lock:
            try:
                                                
                actions = list(ACTION_WEIGHTS.keys())
                weights = list(ACTION_WEIGHTS.values())
                action = random.choices(actions, weights=weights, k=1)[0]
                
                if action == ACTION_IDLE:
                                                                        
                    return True
                
                                             
                if not self.hwnd or not win32gui.IsWindow(self.hwnd):
                    return False
                
                from input import send_key_hold_to_window
                
                if action == ACTION_MOVE:
                                                            
                    key = random.choice(MOVE_KEYS)
                    hold_time = random.uniform(0.1, 0.4)
                    send_key_hold_to_window(self.hwnd, key, hold_time)
                    
                elif action == ACTION_JUMP:
                                 
                    hold_time = random.uniform(0.05, 0.15)
                    send_key_hold_to_window(self.hwnd, JUMP_KEY, hold_time)
                    
                elif action == ACTION_CAMERA:
                                     
                    key = random.choice(CAMERA_KEYS)
                    hold_time = random.uniform(0.1, 0.3)
                    send_key_hold_to_window(self.hwnd, key, hold_time)
                
                return True
                    
            except Exception as e:
                return False

    def antiafk_focused(self):
        with client_lock:
            try:
                if not self.hwnd or not win32gui.IsWindow(self.hwnd):
                    return False

                self.focus()
                time.sleep(0.05)

                action = random.choice([ACTION_MOVE, ACTION_JUMP, ACTION_CAMERA])
                if action == ACTION_MOVE:
                    key = random.choice(MOVE_KEYS)
                    hold = random.uniform(0.12, 0.35)
                elif action == ACTION_JUMP:
                    key = JUMP_KEY
                    hold = random.uniform(0.05, 0.12)
                else:
                    key = random.choice(CAMERA_KEYS)
                    hold = random.uniform(0.08, 0.25)

                press_key(key)
                time.sleep(hold)
                release_key(key)
                return True
            except Exception:
                return False

class Roblox:
    def __init__(self, ROBLOSECURITY=None, manager=None):
        self.manager = manager or PoolManager(cert_reqs='CERT_NONE')
        self.csrf_token = None
        self.browser_tracker_id = random.randint(1, 1231324234)
        self.ROBLOSECURITY = None
        self.cookie_tracker = None                               
        self.id = None
        self.name = None
        if ROBLOSECURITY:
            self.auth_from_cookie(ROBLOSECURITY)
            
            
    def __repr__(self):
        if self.id:
            return self.name
        else:
            return "Unauthenticated"

    def auth_from_cookie(self, ROBLOSECURITY):
        self.ROBLOSECURITY = ROBLOSECURITY

        auth_info = self.get_auth()
        if not auth_info:
            raise Exception("Invalid or expired .ROBLOSECURITY cookie")

        self.id = auth_info["id"]
        self.name = auth_info["name"]

    def get_cookies(self, host):
        cookies = {}
        if host.lower().endswith(".roblox.com"):
            if self.ROBLOSECURITY:
                cookies[".ROBLOSECURITY"] = self.ROBLOSECURITY
        return cookies
    
    def get_headers(self, method, host):
        headers = {}
        if host.lower().endswith(".roblox.com"):
            headers["Origin"] = "https://www.roblox.com"
            headers["Referer"] = "https://www.roblox.com/"
            if method == "POST":
                headers["Content-Type"] = "application/json"
                if self.csrf_token:
                    headers["X-CSRF-TOKEN"] = self.csrf_token
        return headers

    def get_auth(self):
        r = self.request("GET", "https://users.roblox.com/v1/users/authenticated")
        return r.status == 200 and r.json()
    
    def request(self, method, url, headers={}, data=None):
        purl = urlsplit(url)
        data = data and json.dumps(data, separators=(",",":"))
        headers.update(self.get_headers(method, purl.hostname))
        cookies = self.get_cookies(purl.hostname)
        if cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k,v in cookies.items())

        resp = self.manager.request(
            method=method,
            url=url,
            headers=headers,
            body=data
        )

        if "x-csrf-token" in resp.headers:
            self.csrf_token = resp.headers["x-csrf-token"]
            return self.request(method, url, headers, data)

                                  
        if self.cookie_tracker:
            self.cookie_tracker.check_and_update(resp.headers)
                                                           
            if self.cookie_tracker.current_cookie != self.ROBLOSECURITY:
                self.ROBLOSECURITY = self.cookie_tracker.current_cookie

        resp.json = lambda: json.loads(resp.data)
        return resp

    def create_client(self, place_id, job_id=None, launch_data=None):
        return Client(self, place_id, job_id, launch_data)
