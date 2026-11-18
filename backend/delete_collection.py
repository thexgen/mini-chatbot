from qdrant_client import QdrantClient

client = QdrantClient(url="http://localhost:6333", api_key="")

client.delete_collection(collection_name="mini_chat_documents")