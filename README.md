# LAN-Based Multi-User Collaborative Whiteboard

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Networking](https://img.shields.io/badge/Networking-TCP%2FIP%20Sockets-orange.svg)](#networking-architecture)
[![Architecture](https://img.shields.io/badge/Architecture-Authoritative%20Server-purple.svg)](#architecture-overview)

A high-performance, real-time, multi-user collaborative whiteboard and group communication system designed strictly for Local Area Networks (LAN). Built from the ground up using **pure Python low-level TCP/IP socket programming**, this project highlights core Computer Networks principles, custom application-layer message framing, non-blocking concurrent architectures, distributed state synchronization, and zero-configuration UDP discovery.

---

## Table of Contents
1. [Key Features](#key-features)
2. [Computer Networks Engineering Concepts](#computer-networks-engineering-concepts)
3. [Architecture Overview](#architecture-overview)
4. [Custom Application-Layer Protocol](#custom-application-layer-protocol)
5. [Installation & Requirements](#installation--requirements)
6. [Quick Start Guide](#quick-start-guide)
   - [Running on a Single Computer (Loopback)](#running-on-a-single-computer-loopback)
   - [Running across Multiple LAN Devices (Wi-Fi / Ethernet)](#running-across-multiple-lan-devices-wi-fi--ethernet)
7. [GUI & Interactive Controls](#gui--interactive-controls)
8. [Server Management CLI](#server-management-cli)
9. [Running Automated Tests](#running-automated-tests)
10. [Academic Lab Documentation & Viva Guide](#academic-lab-documentation--viva-guide)

---

## Key Features

- **Real-Time Freehand Drawing**: Sub-millisecond synchronized mouse strokes across all connected peers with coordinate interpolation.
- **Geometric Shapes**: Interactive rubber-band previews for Straight Lines, Rectangles, Circles/Ovals, and Directed Arrows.
- **Drawing Tools & Controls**:
  - Full color palette + custom system color picker.
  - Variable stroke width slider (1px to 40px) with live size indicator.
  - Dedicated Whiteboard Eraser tool.
- **Collaborative Non-Destructive Undo & Redo**:
  - User-centric undo: Undoing reverts the requesting user's most recent action without wiping actions made concurrently by other participants.
  - Synchronized state broadcast across all peers.
- **Integrated Real-Time Group Chat**:
  - Embedded sidebar chat with user-specific color tags and timestamps.
  - System announcements for user connections, disconnections, and canvas clears.
- **Live Connected Participants Roster**:
  - Real-time online participant count and user list with assigned drawing color swatches.
- **Zero-Configuration LAN Auto-Discovery**:
  - Server broadcasts UDP beacons (`255.255.255.255:5051`).
  - Clients can auto-scan the local subnet and connect with one click without manually typing IP addresses.
- **Session State Persistence & Image Export**:
  - Save session state to JSON files and reload saved sessions onto the live collaborative canvas.
  - Export the whiteboard canvas to high-resolution PNG or JPEG images using Pillow.
- **Late-Join State Synchronization**:
  - When a new participant connects mid-session, the authoritative server automatically transmits the complete action history, reconstructing the identical whiteboard state instantly.
- **100% Offline / No Internet Dependency**:
  - Operates completely without external internet access, cloud services, or third-party brokers.

---

## Computer Networks Engineering Concepts

| Concept | Implementation in this Project |
| :--- | :--- |
| **Transport Layer (OSI Layer 4)** | Raw TCP sockets (`AF_INET`, `SOCK_STREAM`) for reliable, in-order whiteboard and chat data delivery. UDP sockets (`SOCK_DGRAM`, `SO_BROADCAST`) for LAN auto-discovery beacons. |
| **TCP Stream Framing** | Solves the TCP segment fragmentation and packet coalescing ("sticky packets") problem by prepending a 4-byte big-endian length prefix (`!I`) to every message frame. |
| **Nagle's Algorithm (`TCP_NODELAY`)** | Disabled on all client and server sockets (`TCP_NODELAY = 1`) to eliminate the 40ms buffering delay on small coordinate packets, achieving instantaneous stroke streaming. |
| **Socket Options (`SO_REUSEADDR`)** | Enabled on TCP and UDP sockets to prevent `EADDRINUSE` errors and allow immediate server reboots without waiting for the OS `TIME_WAIT` state to expire. |
| **Concurrency & Threading** | Multi-threaded server using a Thread-per-Client architecture with synchronization locks (`threading.RLock`) around client rosters and authoritative canvas state. |
| **Decoupled GUI Threading** | Client isolates socket network I/O in a background thread and communicates with the Tkinter main loop through a thread-safe `queue.Queue`, preventing UI freezes and race conditions. |
| **Connection Health & Half-Open TCP** | Periodic bidirectional heartbeat messages (`HEARTBEAT` ping/pong) detect broken cables or ungraceful client terminations. |

---

## Architecture Overview

```
                      +---------------------------------------+
                      |       Central Authoritative           |
                      |          TCP Server                   |
                      |   (Thread-per-Client, State History)  |
                      +-------------------+-------------------+
                                          |
                +-------------------------+-------------------------+
                |                         |                         |
        TCP Stream Socket         TCP Stream Socket         TCP Stream Socket
        (Length-Prefixed)         (Length-Prefixed)         (Length-Prefixed)
                |                         |                         |
                v                         v                         v
     +--------------------+    +--------------------+    +--------------------+
     |   Client 1 (Alice) |    |   Client 2 (Bob)   |    |  Client 3 (Charlie)|
     | Tkinter GUI Canvas |    | Tkinter GUI Canvas |    | Tkinter GUI Canvas |
     | Integrated Chat    |    | Integrated Chat    |    | Integrated Chat    |
     +--------------------+    +--------------------+    +--------------------+
                ^                         ^                         ^
                |                         |                         |
                +-------------------------+-------------------------+
                                          |
                                UDP Broadcast Beacon
                               (LAN Auto-Discovery)
                                  Port 5051
```

### Server Concurrency Model
1. **Main Thread**: Boots the server, detects the local network interface IP, binds the socket, and spawns the interactive CLI console.
2. **Accept Thread**: Continuously blocks on `server_sock.accept()` to admit new client TCP handshakes.
3. **Client Worker Threads (`ClientHandler`)**: Each connected peer runs on a dedicated thread that continuously reads framed messages, updates the authoritative state, and broadcasts updates.
4. **Discovery Thread (`DiscoveryBeaconServer`)**: Emits UDP broadcast datagrams once every 2 seconds to announce the server's presence on the subnet.

### Client Concurrency Model
1. **Main UI Thread**: Runs Tkinter's event loop (`mainloop()`), handling local user mouse inputs, drawing previews, and widget renders.
2. **Background Network Receiver**: Runs a blocking socket read loop (`read_framed_message()`) and enqueues parsed protocol objects into an internal `queue.Queue`.
3. **Scheduled Dispatcher**: Uses Tkinter's `root.after(15, poll)` to drain incoming events onto the UI thread at ~66 FPS, guaranteeing thread-safe canvas manipulation.
4. **Heartbeat Worker**: Emits periodic keepalive messages every 10 seconds.

---

## Custom Application-Layer Protocol

### Message Framing
Every message transmitted across TCP sockets follows this binary wire frame:
```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                Payload Length (4 Bytes, Big-Endian)           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
+                     Payload (UTF-8 JSON)                      +
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

### Message Types
| Message Type | Direction | Description |
| :--- | :--- | :--- |
| `JOIN` | Client $\to$ Server | Initial handshake with username, client version, and UUID. |
| `JOIN_ACK` | Server $\to$ Client | Approves join, assigns unique user color, sends participant list and full canvas state history. |
| `JOIN_REJECT` | Server $\to$ Client | Rejects join if server is full or username is taken. |
| `LEAVE` | Client $\to$ Server | Graceful disconnect notice. |
| `USER_JOINED` | Server $\to$ Clients | Broadcasts when a new user enters the session. |
| `USER_LEFT` | Server $\to$ Clients | Broadcasts when a participant disconnects or times out. |
| `DRAW_SEGMENT` | Client $\leftrightarrow$ Server | High-frequency mouse movement coordinate segment for live rendering. |
| `DRAW_STROKE` | Client $\leftrightarrow$ Server | Finalized freehand stroke action committed to the canvas history stack. |
| `SHAPE` | Client $\leftrightarrow$ Server | Finalized geometric shape (Line, Rect, Circle, Arrow) committed to canvas. |
| `ERASE` | Client $\leftrightarrow$ Server | Finalized eraser stroke committed to history. |
| `CLEAR` | Client $\leftrightarrow$ Server | Clears the canvas for all participants. |
| `UNDO` | Client $\leftrightarrow$ Server | Undoes the user's last action and broadcasts target action ID to revert on all peers. |
| `REDO` | Client $\leftrightarrow$ Server | Restores the most recently undone action. |
| `CHAT` | Client $\leftrightarrow$ Server | Group chat message with username, color, and timestamp. |
| `SYNC_REQUEST` | Client $\to$ Server | Explicit request to re-fetch full canvas state from server. |
| `SYNC_STATE` | Server $\to$ Client | Delivers full snapshot of all active and undone actions. |
| `LOAD_STATE` | Client $\leftrightarrow$ Server | Replaces the canvas state with an imported JSON session file. |
| `HEARTBEAT` | Client $\leftrightarrow$ Server | Ping/pong keepalive packet to detect broken connections. |

---

## Installation & Requirements

### System Requirements
- **Python**: Version 3.10 or higher.
- **Operating System**: Windows, Linux, or macOS.
- **Network**: Any Local Area Network (Wi-Fi, Ethernet, or local loopback). No internet required.

### Dependencies
The core networking engine, socket framer, threading architecture, and Tkinter GUI use **Python's standard library** with zero external dependencies.

[Pillow](https://pillow.readthedocs.io/) is used for exporting high-resolution canvas snapshots (PNG/JPEG):
```bash
pip install -r requirements.txt
```

---

## Quick Start Guide

### Running on a Single Computer (Loopback)

You can run both the server and multiple client instances on one machine to test multi-user collaboration:

1. **Start the Central Server**:
   Open a terminal and run:
   ```bash
   python server_main.py
   ```
   The server will print its listening address and detected LAN IP (e.g., `192.168.1.10:5050`).

2. **Launch Client 1 (Alice)**:
   Open a second terminal and run:
   ```bash
   python client_main.py --username Alice --auto-connect
   ```

3. **Launch Client 2 (Bob)**:
   Open a third terminal and run:
   ```bash
   python client_main.py --username Bob --auto-connect
   ```

Alice and Bob can now draw simultaneously, send chat messages, use undo/redo, and observe live updates with zero delay!

---

### Running across Multiple LAN Devices (Wi-Fi / Ethernet)

To collaborate with other computers, laptops, or Raspberry Pis on the same Wi-Fi router or Ethernet switch:

1. **Start the Server on the Host Machine**:
   ```bash
   python server_main.py
   ```
   Note the displayed **Primary LAN IP** (for example: `192.168.1.45`).

2. **Connect Clients from Other Machines on the Same LAN**:
   Launch the client application:
   ```bash
   python client_main.py
   ```
   In the Connection Dialog:
   - Click **"🔍 Scan LAN for Servers"** to let UDP auto-discovery find the server automatically, **OR**
   - Enter your Username, enter the Host IP (`192.168.1.45`), and Port `5050`.
   - Click **Connect**.

> **Note on Windows / Linux Firewalls**: If connecting across separate physical machines, ensure your firewall permits incoming TCP connections on port `5050` and incoming UDP broadcasts on port `5051`.

---

## GUI & Interactive Controls

```
+-------------------------------------------------------------------------------+
| File  Edit  Tools  Network  Help                                              |
+-------------------------------------------------------------------------------+
| [✏️ Pen] [📏 Line] [⬜ Rect] [⭕ Circle] [➡️ Arrow] [🧹 Eraser]               |
| Colors: [■][■][■][■][■] [🎨 More...]  Stroke Width: [=====o===] 5px          |
| [↩ Undo] [↪ Redo] [🗑 Clear] [💾 Save] [🖼 Export]                            |
+-------------------------------------------------------+-----------------------+
|                                                       | Participants (3)      |
|                                                       | ● Alice (You)         |
|                                                       | ● Bob                 |
|               SHARED WHITEBOARD CANVAS                | ● Charlie             |
|                                                       +-----------------------+
|                 (Real-Time Drawing)                   | Group Chat            |
|                                                       | [10:15] Alice: Hi all |
|                                                       | [10:15] Bob: Hello!   |
|                                                       | [Message:        ][S] |
+-------------------------------------------------------+-----------------------+
| 🟢 Connected (192.168.1.45:5050) | User: Alice | Tool: Pen | Actions: 12      |
+-------------------------------------------------------------------------------+
```

### Keyboard Shortcuts
- `Ctrl + Z`: Undo last action
- `Ctrl + Y`: Redo undone action
- `Ctrl + S`: Save session state to JSON file
- `Ctrl + E`: Export canvas to PNG image
- `Ctrl + Shift + C`: Clear canvas for everyone

---

## Server Management CLI

The server process provides an interactive command console while running:

```
Available Commands:
  status        - Displays server uptime, number of connected clients, and action count.
  clients       - Lists all connected client IP addresses, usernames, and assigned colors.
  clear         - Authoritatively wipes the canvas for all connected clients.
  save [file]   - Saves the current collaborative whiteboard state to a JSON file.
  export [file] - Renders and exports the current whiteboard state as a PNG image.
  quit / exit   - Gracefully notifies all clients and stops the server.
```

---

## Running Automated Tests

A comprehensive unit and integration test suite is included in the [`tests/`](file:///c:/Computer%20Network%20Project/tests/) directory:

- `test_protocol.py`: Verifies length-prefixed framing, handling of TCP fragmentation (byte-by-byte feeds), packet coalescing (sticky packets), and JSON message serialization.
- `test_state.py`: Verifies whiteboard state tracking, collaborative user-centric undo/redo, canvas clearing, JSON file persistence, and Pillow image rendering.
- `test_network.py`: Full end-to-end integration test creating a real local TCP server, connecting multiple concurrent clients, exchanging live drawing segments, chat messages, undo events, and verifying late-join state sync.

Run the entire suite with:
```bash
python -m unittest discover tests
```

---

## Academic Lab Documentation & Viva Guide

For students and instructors, complete academic laboratory materials are located in the [`docs/`](file:///c:/Computer%20Network%20Project/docs/) folder:

1. **[docs/NETWORKING_ARCHITECTURE.md](file:///c:/Computer%20Network%20Project/docs/NETWORKING_ARCHITECTURE.md)**:
   - Deep dive into OSI Layer 4 vs Layer 7.
   - Low-level socket syscalls (`socket`, `bind`, `listen`, `accept`, `connect`, `sendall`, `recv`).
   - Detailed analysis of Nagle's algorithm and `TCP_NODELAY`.
   - Thread synchronization and deadlock prevention.
2. **[docs/LAB_MANUAL_GUIDE.md](file:///c:/Computer%20Network%20Project/docs/LAB_MANUAL_GUIDE.md)**:
   - Formal lab record format: Aim, Apparatus, Theory, Network Topology, Algorithm.
   - Step-by-step Wireshark packet capture analysis guide (TCP 3-way handshake, payload examination).
   - 20+ comprehensive Viva Voce questions and detailed answers.
