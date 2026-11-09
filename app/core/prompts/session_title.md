Generate a short title for a conversation based on the user's first message.

Rules:
- 3 to 6 words maximum
- Match the language of the user's message
- No quotes, no trailing punctuation, no prefixes like "Title:" or "Subject:"
- Capture the specific topic, not a generic description

Respond with JSON only, in the form {"title": "<the title>"}.

User message:
{user_message}
