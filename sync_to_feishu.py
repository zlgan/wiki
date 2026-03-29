import os
import sys
import requests
import json
from pathlib import Path

# 飞书 API 基础 URL
FEISHU_HOST = "https://open.feishu.cn/open-apis"

# 从环境变量读取配置
APP_ID = os.environ.get("FEISHU_APP_ID")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET")
SPACE_ID = os.environ.get("FEISHU_SPACE_ID")

if not all([APP_ID, APP_SECRET, SPACE_ID]):
    print("缺少必要的环境变量: FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_SPACE_ID")
    sys.exit(1)

# 需要同步的 Markdown 文件根目录（仓库根目录）
ROOT_DIR = Path(".")

# 如果需要指定某个文件夹作为同步的起点，可以在下面设置其 node_token
PARENT_NODE_TOKEN = None   # 保持 None 表示从知识库根目录开始同步


def get_tenant_access_token():
    """获取 tenant_access_token"""
    url = f"{FEISHU_HOST}/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": APP_ID,
        "app_secret": APP_SECRET
    }
    resp = requests.post(url, json=payload)
    if resp.status_code != 200:
        raise Exception(f"获取 token 失败: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取 token 失败: {data}")
    return data["tenant_access_token"]


def find_node_by_path(token, path_parts):
    """根据路径查找节点，返回节点 token，若不存在返回 None
    path_parts: list of folder names + file name (不含 .md 后缀)
    """
    current_parent_token = PARENT_NODE_TOKEN  # 初始父节点，None 表示从空间根目录开始

    # 逐层查找
    for i, name in enumerate(path_parts):
        # 获取当前父节点下的子节点
        url = f"{FEISHU_HOST}/wiki/v2/spaces/{SPACE_ID}/nodes"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"page_size": 50}
        if current_parent_token:
            params["parent_node_token"] = current_parent_token
        
        found = None
        while True:
            resp = requests.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                raise Exception(f"获取节点列表失败 (HTTP {resp.status_code}): {resp.text}")
            data = resp.json()
            if data.get("code") != 0:
                raise Exception(f"获取节点列表失败 (Code {data.get('code')}): {data.get('msg')}")
            
            items = data.get("data", {}).get("items", [])
            for item in items:
                if item.get("title") == name:
                    found = item
                    break
            if found:
                break
            
            # 分页处理
            page_token = data.get("data", {}).get("page_token")
            has_more = data.get("data", {}).get("has_more", False)
            if not page_token or not has_more:
                break
            params["page_token"] = page_token
            
        if not found:
            return None
            
        current_parent_token = found["node_token"]
        
        if i == len(path_parts) - 1:
            return found["node_token"]
            
    return None


def create_node(token, parent_node_token, title, node_type, content=None):
    """创建节点"""
    url = f"{FEISHU_HOST}/wiki/v2/spaces/{SPACE_ID}/nodes"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "obj_type": node_type,
        "title": title
    }
    if parent_node_token:
        payload["parent_node_token"] = parent_node_token
        
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code != 200:
        raise Exception(f"创建节点失败 (HTTP {resp.status_code}): {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"创建节点失败 (Code {data.get('code')}): {data.get('msg')}")
    
    node_token = data["data"]["node"]["node_token"]
    if content:
        update_document_content(token, node_token, content)
        
    return node_token


def update_document_content(token, node_token, content):
    """更新文档内容"""
    url = f"{FEISHU_HOST}/wiki/v2/nodes/{node_token}"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise Exception(f"获取节点信息失败 (HTTP {resp.status_code}): {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取节点信息失败 (Code {data.get('code')}): {data.get('msg')}")
    
    node_data = data["data"]["node"]
    obj_token = node_data["obj_token"]
    obj_type = node_data["obj_type"]
    
    if obj_type != "docx":
        print(f"警告: 节点 {node_token} 类型为 {obj_type}，非 docx，跳过内容更新")
        return

    update_url = f"{FEISHU_HOST}/docx/v1/documents/{obj_token}/raw_content"
    headers["Content-Type"] = "application/json"
    payload = {"content": content}
    resp = requests.put(update_url, headers=headers, json=payload)
    if resp.status_code != 200:
        raise Exception(f"更新文档内容失败 (HTTP {resp.status_code}): {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"更新文档内容失败 (Code {data.get('code')}): {data.get('msg')}")
    print(f"文档 {node_token} 内容已更新")


def sync_file(file_path, token):
    """同步单个 Markdown 文件"""
    rel_path = file_path.relative_to(ROOT_DIR)
    parts = list(rel_path.parts)
    if parts[-1].endswith(".md"):
        parts[-1] = parts[-1][:-3]
    else:
        return

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    node_token = find_node_by_path(token, parts)
    if node_token:
        update_document_content(token, node_token, content)
        print(f"已更新: {rel_path}")
    else:
        current_parent = PARENT_NODE_TOKEN
        for i, name in enumerate(parts[:-1]):
            found = find_node_by_path(token, parts[:i+1])
            if found:
                current_parent = found
            else:
                current_parent = create_node(token, current_parent, name, "docx")
                print(f"创建文件夹节点: {name}")

        create_node(token, current_parent, parts[-1], "docx", content=content)
        print(f"创建文档: {rel_path}")


def main():
    try:
        token = get_tenant_access_token()
    except Exception as e:
        print(f"身份验证失败: {e}")
        sys.exit(1)

    import subprocess
    try:
        diff_cmd = ["git", "diff", "--name-only", "HEAD~1", "HEAD"]
        changed_files = subprocess.check_output(diff_cmd, stderr=subprocess.STDOUT).decode().splitlines()
    except subprocess.CalledProcessError:
        print("无法获取增量更新，将同步所有 Markdown 文件")
        changed_files = subprocess.check_output(["git", "ls-files"]).decode().splitlines()

    md_files = [Path(f) for f in changed_files if f.endswith(".md") and Path(f).exists()]

    if not md_files:
        print("没有需要同步的 Markdown 文件")
        return

    print(f"准备同步 {len(md_files)} 个文件...")
    for md_file in md_files:
        try:
            sync_file(md_file, token)
        except Exception as e:
            print(f"同步 {md_file} 失败: {e}")


if __name__ == "__main__":
    main()
