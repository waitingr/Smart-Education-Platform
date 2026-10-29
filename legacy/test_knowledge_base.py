# test_knowledge_base.py
from pathlib import Path
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VECTOR_DB_PATH = str(PROJECT_ROOT / "data" / "vector_db")

print("=" * 50)
print("知识库检索测试")
print("=" * 50)

# 加载向量库
print("\n加载知识库...")
embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2",
    model_kwargs={'device': 'cpu'},
    encode_kwargs={'normalize_embeddings': True}
)
vector_db = FAISS.load_local(
    VECTOR_DB_PATH, 
    embeddings, 
    allow_dangerous_deserialization=True
)
print("✅ 知识库加载成功！")

# 测试查询
print("\n" + "=" * 50)
print("请输入你的问题（输入 q 退出）：")
print("=" * 50)

while True:
    query = input("\n🔍 问题：").strip()
    if query.lower() in ['q', 'quit', 'exit']:
        print("退出测试")
        break
    if not query:
        continue
    
    # 检索前 3 个最相关的片段
    results = vector_db.similarity_search(query, k=3)
    
    print(f"\n📚 找到 {len(results)} 个相关片段：")
    for i, doc in enumerate(results):
        print(f"\n--- 结果 {i+1} ---")
        print(f"内容：{doc.page_content[:300]}...")
        print(f"来源：{doc.metadata.get('source', '未知')}")
    
    print("\n" + "-" * 40)