# Cohere v2 REST API

verified: 2026-10-06

 Primary references: https://docs.cohere.com/reference/chat and https://docs.cohere.com/reference/chat-stream . Embed reference: https://docs.cohere.com/v2/reference/embed and https://docs.cohere.com/v2/docs/embeddings (verified mixed-content schema, 96 inputs and 1024-dimension support).

Uses httpx with bearer COHERE_API_KEY. Chat: command-a-03-2025, system/user messages, optional response_format json_object/json_schema; prompts explicitly request JSON. Response message.content text blocks; usage.billed_units for tokens. Streaming: content-delta.delta.message.content.text and message-end.delta.usage; require message-end before success.

Embed: embed-v4.0, search_document/search_query, embedding_types [float], output_dimension 1024, inputs with text and image_url content segments (PNG data URI). Batch cap 96; validate count, dimensions and finite values. No keys stored in repository.
