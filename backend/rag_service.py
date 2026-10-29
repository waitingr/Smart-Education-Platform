"""向后兼容层：保留旧版 rag_query / init_rag / stop_generation 接口。

旧版实现会在 import 时启动线程加载 14GB 模型，导致无 GPU 机器启动即卡死。
新版改为：知识库按需构建、大模型惰性加载、生成统一走 agents.tutor 工作流。
旧调用方（如 rag_qa.py、历史脚本）无需修改即可继续使用。
"""
import threading

from kb_service import get_kb, rebuild_kb
from llm_client import get_llm

_stop_flag = threading.Event()


def stop_generation():
    _stop_flag.set()
    print('[RAG] 收到停止生成信号')


def reset_generation_flag():
    _stop_flag.clear()


def is_stopped():
    return _stop_flag.is_set()


def init_rag():
    """预热知识库（不再加载大模型，模型在首次生成时惰性加载）。"""
    try:
        kb = get_kb()
        stats = kb.stats()
        print(f"[RAG] 知识库就绪：{stats['doc_count']} 篇文档 / {stats['chunk_count']} 个知识块")
        return True
    except Exception as error:
        print(f'[RAG] 知识库初始化失败：{error}')
        return False


def rag_query(question, top_k=3, max_tokens=None):
    """兼容旧接口：返回 answer / sources / context 三个字段。"""
    from agents import run_agent

    reset_generation_flag()
    try:
        payload = run_agent('tutor', {'question': question}, profile={'level': 'intermediate'})
    except Exception as error:
        return {'answer': f'⚠️ 生成回答时出错：{error}', 'sources': [], 'context': '', 'citations': []}
    if _stop_flag.is_set():
        return {'answer': '⏹️ 生成已取消', 'sources': [], 'context': '', 'citations': []}
    citations = payload.get('citations') or []
    return {
        'answer': payload.get('answer', ''),
        'sources': [item['locator'] for item in citations],
        'citations': citations,
        'context': '',
        'trace': payload.get('trace', []),
        'degraded': payload.get('degraded', True),
        'provider': payload.get('provider', 'offline'),
    }


def knowledge_base_status():
    try:
        return {'ready': True, **get_kb().stats()}
    except Exception as error:
        return {'ready': False, 'error': str(error)}


if __name__ == '__main__':
    init_rag()
    while True:
        question = input('\nYou: ').strip()
        if question.lower() in ('q', 'quit', 'exit'):
            break
        if not question:
            continue
        result = rag_query(question)
        print('\nAssistant:', result['answer'])
        for source in result.get('sources', []):
            print('  -', source)