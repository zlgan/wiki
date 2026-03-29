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
    print("缺少必要的环境变量")
    sys.exit(1)

# 需要同步的 Markdown 文件根目录（仓库根目录）
ROOT_DIR = Path(".")

# 知识库根节点 ID（通常是知识库空间的根节点，可通过 API 获取）
# 这里假设根节点就是空间本身，创建节点时 parent_node 可设为 None
# 如果需要指定某个文件夹作为根，可以在下面设置
PARENT_NODE_TOKEN = None   # 如果从根开始，保持 None，API 会识别


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


def get_space_root_node(token):
    """获取知识库空间的根节点 token（用于创建顶级节点）"""
    url = f"{FEISHU_HOST}/wiki/v2/spaces/{SPACE_ID}/nodes"
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise Exception(f"获取空间节点失败: {resp.text}")
    data = resp.json()
    if data.get("code") != 0:
        raise Exception(f"获取空间节点失败: {data}")
    # 根节点通常只有一个，取第一个
    nodes = data.get("data", {}).get("items", [])
    if not nodes:
        # 如果空间没有根节点，可能需要创建？通常自动存在
        raise Exception("未找到空间根节点")
    return nodes[0]["node_token"]


def find_node_by_path(token, path_parts):
    """根据路径查找节点，返回节点信息字典，若不存在返回 None
    path_parts: list of folder names + file name (不含 .md 后缀)
    """
    current_parent_token = PARENT_NODE_TOKEN

    # 逐层查找
    for i, name in enumerate(path_parts):
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
            
            page_token = data.get("data", {}).get("page_token")
            has_more = data.get("data", {}).get("has_more", False)
            if not page_token or not has_more:
                break
            params["page_token"] = page_token
            
        if not found:
            return None
            
        current_parent_token = found["node_token"]
        
        if i == len(path_parts) - 1:
            # 返回完整的节点信息，包含 node_token, obj_token, obj_type 等
            return found
            
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
        "node_type": "origin",
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
    
    node_data = data["data"]["node"]
    
    # 如果有内容，创建后更新内容
    if content:
        update_document_content(token, node_data, content)
        
    return node_data


def update_document_content(token, node_data, content):
    """更新文档内容
    node_data: 节点信息字典，需包含 node_token, obj_token, obj_type
    """
    node_token = node_data["node_token"]
    obj_token = node_data["obj_token"]
    obj_type = node_data["obj_type"]
    
    if obj_type != "docx":
        print(f"警告: 节点 {node_token} 类型为 {obj_type}，非 docx，跳过内容更新")
        return

    # 更新文档内容
    update_url = f"{FEISHU_HOST}/docx/v1/documents/{obj_token}/raw_content"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
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

    # 查找节点
    node_info = find_node_by_path(token, parts)
    if node_info:
        # 更新内容
        update_document_content(token, node_info, content)
        print(f"已更新: {rel_path}")
    else:
        # 创建节点（需要逐层创建文件夹）
        current_parent_token = PARENT_NODE_TOKEN

        # 创建路径上的所有中间节点（作为父文件夹）
        for i, name in enumerate(parts[:-1]):
            # 查找当前层是否存在该节点
            found = find_node_by_path(token, parts[:i+1])
            if found:
                current_parent_token = found["node_token"]
            else:
                # 创建中间节点
                new_node = create_node(token, current_parent_token, name, "docx")
                current_parent_token = new_node["node_token"]
                print(f"创建中间节点: {name}")

        # 创建最终文档节点
        create_node(token, current_parent_token, parts[-1], "docx", content=content)
        print(f"创建文档: {rel_path}")


def main():
    # 获取 token
    try:
        token = get_tenant_access_token()
    except Exception as e:
        print(f"身份验证失败: {e}")
        sys.exit(1)

    # 获取本次提交变更的 Markdown 文件
    import subprocess
    try:
        # 尝试获取最近一次提交变更的文件列表
        # 在 GitHub Actions 中建议设置 fetch-depth: 0
        diff_cmd = ["git", "diff", "--name-only", "HEAD~1", "HEAD"]
        changed_files = subprocess.check_output(diff_cmd, stderr=subprocess.STDOUT).decode().splitlines()
    except subprocess.CalledProcessError:
        # 如果是第一次提交或者 HEAD~1 不存在，则列出所有文件
        print("无法获取增量更新，将同步所有 Markdown 文件")
        diff_cmd = ["git", "ls-files"]
        changed_files = subprocess.check_output(diff_cmd).decode().splitlines()

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
