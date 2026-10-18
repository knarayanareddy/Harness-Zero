# Ask User tool

The `tool-ask-user` plugin registers a read-only `ask_user` tool for decisions that materially change the result. It accepts one to four short questions, each with two to four labeled options and optional custom text.

The tool blocks until an interactive web or CLI client answers. Answers are returned to the model as tool output and stored as durable `question.asked` / `question.answered` transcript records. In headless runs it returns an actionable error immediately instead of guessing or waiting for a timeout.

Question and option identifiers are stable keys; labels are shown to the user and returned in readable tool output. Custom text is optional unless the user chooses the custom-answer option.
