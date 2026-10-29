#!/usr/bin/env python3
"""
Qwen RAG System 测试脚本
用于验证系统各组件是否正常工作
"""

import sys
import os
import requests
import json

# 添加项目根目录和 backend 路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'backend'))

def test_rag_service():
    """测试RAG服务"""
    print("=== 测试 RAG 服务 ===")
    try:
        from rag_service import rag_query
        result = rag_query("什么是Python？")
        print("RAG查询结果:", result)
        return True
    except Exception as e:
        print("RAG服务测试失败:", str(e))
        return False

def test_flask_backend():
    """测试Flask后端"""
    print("\n=== 测试 Flask 后端 ===")
    try:
        # 测试健康检查接口
        response = requests.get("http://localhost:5000/api/v1/health", timeout=5)
        if response.status_code == 200:
            print("后端健康检查通过:", response.json())
            return True
        else:
            print("后端健康检查失败:", response.status_code)
            return False
    except Exception as e:
        print("后端连接失败:", str(e))
        return False

def test_frontend():
    """测试前端文件"""
    print("\n=== 测试 前端文件 ===")
    frontend_path = os.path.join(PROJECT_ROOT, 'frontend', 'index.html')
    if os.path.exists(frontend_path):
        print("前端文件存在:", frontend_path)
        return True
    else:
        print("前端文件不存在:", frontend_path)
        return False

def main():
    print("Qwen RAG System 测试")
    print("=" * 50)
    
    results = {}
    
    # 测试各组件
    results['frontend'] = test_frontend()
    results['rag'] = test_rag_service()
    results['backend'] = test_flask_backend()
    
    # 汇总结果
    print("\n" + "=" * 50)
    print("测试结果汇总:")
    for component, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{component}: {status}")
    
    # 总体评估
    if all(results.values()):
        print("\n🎉 所有测试通过！系统可以正常使用。")
        return 0
    else:
        print("\n⚠️ 部分测试失败，请检查相关组件。")
        return 1

if __name__ == "__main__":
    sys.exit(main())