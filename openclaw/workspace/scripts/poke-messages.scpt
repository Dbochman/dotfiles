on run
  -- Lightweight query keeps Messages' data pipeline warm without UI activation.
  tell application "Messages"
    set _chatCount to count of chats
  end tell
end run
