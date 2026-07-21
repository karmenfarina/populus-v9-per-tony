#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================

user_problem_statement: |
  Add a full messaging feature to Populus:
  - New chat icon in the bottom tab bar with red unread-count badge.
  - Direct 1-to-1 chat between registered users only (anonymous users blocked from send AND receive).
  - Chat with text + images + emojis + reactions.
  - Read receipts (single check delivered, double check read).
  - Real-time delivery via WebSocket.
  - External push notification when a new message arrives (respect user's push_notifications flag).
  - Block and report user features.
  - Access chat from a user's public profile via "Invia messaggio" button and top-right menu (block/report).

backend:
  - task: "Messaging endpoints (send, list convos, fetch, mark read, react, delete)"
    implemented: true
    working: "NA"
    file: "/app/backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: |
            Added conversations + messages + user_blocks + user_reports collections and full REST surface:
            /api/messages/unread-count, /api/messages/conversations, /api/messages/with/{other},
            /api/messages/send, /api/messages/with/{other}/read, /api/messages/{id}/react,
            /api/messages/{id} DELETE. Anonymous users return 403 on all send/receive endpoints;
            unread-count returns {count:0} for them gracefully. Blocked pairs are enforced bidirectionally.
            Smoke tested manually via curl: send/receive/read/react/block/unblock/report all pass.
  - task: "WebSocket real-time delivery /api/ws/messages"
    implemented: true
    working: "NA"
    file: "/app/backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: |
            Registered WS endpoint at app level (not APIRouter) with JWT / session-token auth via
            query param. Publishes typed events: message.new, message.sent, message.read,
            message.reaction, message.deleted. Anonymous users rejected with close code 4403.
  - task: "Push notification on new message (offline recipient only)"
    implemented: true
    working: "NA"
    file: "/app/backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: |
            When sender POSTs /messages/send, backend checks if recipient has an active WS connection.
            If NOT online AND push_notifications flag is on (default), fires an Emergent push with
            title 'Nuovo messaggio da @<nick>', body preview, action_url /messages/<sender_id>.
            Never blocks main send; wrapped in try/except.
  - task: "Block & Report user endpoints"
    implemented: true
    working: "NA"
    file: "/app/backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: |
            /api/users/{uid}/block (POST/DELETE), /api/users/me/blocks (GET), /api/users/{uid}/report (POST).
            Blocked pairs filter out from conversation list and reject sends with 403.

frontend:
  - task: "New Messaggi tab in bottom bar with unread badge"
    implemented: true
    working: "NA"
    file: "/app/frontend/app/(tabs)/_layout.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: |
            Added between the TOP and NOTIFICHE tabs. Uses chatbubbles icon and a red circular badge
            with unread count (99+ cap) sourced from MessagingContext.
  - task: "Conversations list screen"
    implemented: true
    working: "NA"
    file: "/app/frontend/app/(tabs)/messages/index.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: |
            Rows show avatar, nickname, last-message preview (with 'Tu:' prefix if sender is me),
            relative timestamp, unread bubble. LIVE indicator in the header shows WS connection state.
            Anonymous users see a locked screen.
  - task: "1-to-1 chat screen with text, images, reactions, read receipts"
    implemented: true
    working: "NA"
    file: "/app/frontend/app/(tabs)/messages/[userId].tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: |
            WhatsApp-style bubbles (right red / left surface). Long-press bubble → reactions modal (8 emojis)
            + delete for own messages. Image picker via expo-image-picker (base64, quality 0.6).
            Emoji picker modal. Send button. Read receipts (single vs double check with color when read).
            Composer disabled when either side has blocked. Real-time updates via MessagingContext.subscribe().
            Marks all incoming as read on open.
  - task: "MessagingContext (WebSocket + unread counter + pub/sub)"
    implemented: true
    working: "NA"
    file: "/app/frontend/src/messaging/MessagingContext.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: |
            Connects a WebSocket to /api/ws/messages on login, exposes unread + connected + subscribe().
            Reconnects with exponential backoff (max 30s). Pings every 25s. Falls back to 30s polling
            of /messages/unread-count if WS drops. Skipped entirely for anonymous users.
  - task: "Invia messaggio button + menu (block/report) in user profile"
    implemented: true
    working: "NA"
    file: "/app/frontend/app/(tabs)/user/[id].tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: |
            Added a red 'INVIA MESSAGGIO' CTA under the stats, hidden for self/anonymous. Top-right
            3-dot menu opens options: Invia messaggio / Blocca-Sblocca / Segnala. Report modal takes
            free-text reason (2..500 chars).
  - task: "Photo cropper with pan+zoom for profile picture uploads"
    implemented: true
    working: "NA"
    file: "/app/frontend/src/components/PhotoCropper.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: |
            Custom Instagram-style square cropper. Native ImagePicker editing is now disabled
            (allowsEditing:false) so the user always sees our cropper after picking. Users can
            drag the image to pan and use +/− zoom controls (1x–4x). A circular guide overlays
            the crop area to preview the final avatar shape and rule-of-thirds grid helps
            composition. On confirm, expo-image-manipulator crops the actual pixels to the
            selected 1:1 region and downscales to max 1080px @ 0.85 quality JPEG base64.
            Constraints keep the image edges outside the crop window so no empty space is ever
            exposed.

metadata:
  created_by: "main_agent"
  version: "1.0"
  test_sequence: 0
  run_ui: false

test_plan:
  current_focus:
    - "ShareSheet direct deep-link to Instagram/Messenger (skip OS picker)"
    - "@ prefix on nickname (own + external profile) + display_name on external profile"
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

backend_new_tests:
  - task: "PATCH /api/auth/me/profile enforces unique nickname + display_name"
    file: "/app/backend/server.py"
    endpoint: "PATCH /api/auth/me/profile"
    needs_retesting: true
    scenarios:
      - "Same-user re-save with own nickname => 200 (no self-clash)"
      - "Set display_name to non-empty then '' => stored as null"
      - "Try to set nickname that another user already owns (case-insensitive) => 409"

frontend_new_tests:
  - task: "Identity editor modal (nickname + display_name)"
    file: "/app/frontend/app/(tabs)/profile.tsx"
    needs_retesting: true
    scenarios:
      - "Tap 'MODIFICA IDENTITÀ' row => modal opens with current values"
      - "Edit nickname and save => header nickname updates"
      - "Clear display_name and save => grey subtitle disappears"
  - task: "ShareSheet Instagram/Messenger UX"
    file: "/app/frontend/src/components/ShareSheet.tsx"
    needs_retesting: true
    scenarios:
      - "Tap Instagram: link copied to clipboard, Instagram tab opened, green banner shown"
      - "Tap Messenger: link copied, messenger.com opened, green banner shown"
      - "Other providers (whatsapp, telegram, twitter, facebook, email) still build correct URL and close sheet"

agent_communication:
  - agent: "main"
    message: |
      Implemented full messaging system with WebSocket real-time, image/emoji/reactions, read
      receipts, block & report. Anonymous users are blocked from sending or receiving.
      Two pre-verified accounts exist for testing:
        - chat_a@test.it / test123  (user_6e65e19525d5, nickname chatUserA)
        - chat_b@test.it / test123  (user_16f709708760, nickname chatUserB)
      Both have onboarding_completed=true.
  - agent: "testing"
    message: |
      Iteration 28: 19/19 backend pytest + 5/6 frontend flows passing. Reported 2 bugs:
      (H) anonymous lockout screen never shown because is_anonymous field missing in auth response;
      (M) duplicate FlatList key after send due to WS message.sent racing HTTP response.
  - agent: "main"
    message: |
      Applied fixes for iteration 28:
      1) Backend `_public_user` now always includes `is_anonymous` boolean.
      2) Frontend `normalizeUser` in AuthContext back-fills the flag.
      3) Chat send now dedupes by message_id on HTTP response.
      4) ListEmptyComponent wrapped in View to silence Fragment warning.
  - agent: "main"
    message: |
      New feature: PhotoCropper for profile photo uploads. Instagram-style square cropper
      with pan gesture (PanResponder) + zoom controls (1x-4x). Uses expo-image-manipulator
      to crop the actual pixels. Native ImagePicker editing is disabled — our cropper handles
      the crop UI so it works consistently on both mobile and web preview.
  - agent: "testing"
    message: |
      Iteration 30: PhotoCropper static review passed (testIDs, Italian strings, zoom/pan
      clamping, container→image pixel mapping all verified). Bio regression passing. Reported
      two deprecation warnings: `pointerEvents` as prop should be in style, and
      `MediaTypeOptions.Images` deprecated in favor of `['images']`.
      NOTE: Interactive cropper flow could not be tested on web preview because
      expo-image-picker doesn't expose a file input on RN-Web. Requires Expo Go / device test.
  - agent: "main"
    message: |
      Fixed both deprecation warnings from iteration 30:
      1) Moved `pointerEvents="none"` from prop to style for 5 View overlays in PhotoCropper.tsx.
      2) Replaced `ImagePicker.MediaTypeOptions.Images` with `["images"]` in profile.tsx and
         messages/[userId].tsx.
      Cropper feature is code-complete. User must validate the interactive crop flow on Expo Go
      or a real device (web preview cannot deliver a file to expo-image-picker).

  - agent: "main"
    message: |
      Non-destructive re-cropping + slider jump fix.
      Backend (server.py):
        - `PhotoUploadBody` now accepts optional `original_data`.
        - `POST /api/auth/me/photos` stores both `data` (cropped) and `original_data` (uncropped).
        - `PATCH /api/auth/me/photos/{id}` preserves `original_data`; can back-fill it for legacy.
        - New endpoint `GET /api/auth/me/photos/{id}/original` returns the uncropped source
          (falls back to `data` for legacy photos), used by the client when re-cropping so the
          user can zoom back out. `original_data` is intentionally excluded from list responses
          to keep the payload lean.
      Frontend:
        - `api.ts`: `uploadPhoto(data, original_data?)`, `replacePhoto(...)`, `getPhotoOriginal(id)`.
        - `profile.tsx`: source URI is preserved, encoded to a bounded (1440px, q≈0.72) base64
          on upload and shipped as `original_data`. Re-crop flow fetches the original from the
          backend so zoom-out becomes truly non-destructive. Spinner shown on the recrop button
          while the original is being fetched.
        - `PhotoCropper.tsx`: slider tap-to-jump now only triggers when the tap is beyond a 22px
          hitbox around the current thumb — dragging the thumb starts perfectly smoothly, no
          initial snap. Grants outside the thumb still snap-to-tap for fast re-zoom.
      Testing needed:
        - Backend: verify photo upload accepts/stores original_data; replace preserves it; GET
          /original returns it; legacy photos fall back to `data`.
        - Frontend: verify recrop button opens cropper with original source, user can zoom back
          out below previous save, and slider drag has no initial jump.
