# Feature: Settings Enhancement Plan

## Requirements

### 1. Gmail Query Setting (from menu)
- Allow users to edit Gmail query from Settings menu
- Currently: Display only
- Need: Edit form/modal with save functionality
- Storage: Move to database (user_settings table)

### 2. Connect Gmail Button (hide when connected)
- When Gmail is connected, hide "Connect Gmail" button
- Show only "Connected" status and "Disconnect" button

### 3. Schedule (make editable)
- Currently: Static config.yaml
- Need: Edit form with times (e.g., 05:00, 10:00, 14:00, 22:00)
- Storage: Move to database (user_settings table)

### 4. LINE Manual Trigger
- Add "Send Test Message" button to trigger LINE notification manually
- Uses existing LINE integration

## Implementation Plan

### Database Changes
- Add `user_settings` table:
  - id, owner_user_id, key, value, updated_at
- Keys: 'gmail_query', 'schedule', 'line_channel_access_token', 'line_user_id'

### Backend Changes
- Add endpoints:
  - GET/POST /api/settings/gmail-query
  - GET/POST /api/settings/schedule
  - POST /api/settings/line-test (trigger manual send)
- Update config loading to check database first, then fall back to config.yaml

### Frontend Changes
- Update settings.html:
  - Gmail Query: Add edit button/modal
  - Schedule: Add edit button/modal
  - Connect Gmail: Hide button when connected
  - LINE: Add "Send Test" button

## Files to Modify

1. `app/storage/database.py` - Add user_settings table
2. `app/storage/queries.py` - Add queries for user_settings
3. `app/web/routes/settings.py` - Add new endpoints
4. `app/web/templates/settings.html` - Update UI
5. `app/config.py` - Update Settings to load from DB

## Testing

- Test Gmail query edit and save
- Test schedule edit and save  
- Test Connect Gmail hide when connected
- Test LINE manual trigger
