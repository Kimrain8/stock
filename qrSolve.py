import numpy as np

def householder_qr(A):
    m, n = A.shape
    A = A.astype(float).copy()
    Q = np.eye(m)
    
    for k in range(n):
        # 1. 提取第k列，从k行开始的子向量
        x = A[k:, k]
        norm_x = np.linalg.norm(x)
        if norm_x < 1e-10:
            continue
        
        # 2. 确定sigma，保证数值稳定
        sigma = -np.sign(x[0]) * norm_x
        e1 = np.zeros_like(x)
        e1[0] = 1.0
        
        # 3. 构造v, u
        v = x - sigma * e1
        u = v / np.linalg.norm(v)
        
        # 4. 构造低阶H'
        H_prime = np.eye(len(u)) - 2 * np.outer(u, u)
        
        # 5. 扩充为m阶Householder矩阵Hk
        Hk = np.eye(m)
        Hk[k:, k:] = H_prime
        
        # 6. 更新A和Q
        A = Hk @ A
        Q = Q @ Hk
    
    R = np.triu(A)
    return Q, R

# 测试
if __name__ == "__main__":
    A = np.array([[1,2,3],[4,5,6],[7,8,9]])
    Q, R = householder_qr(A)
    print("Q:\n", np.round(Q,4))
    print("R:\n", np.round(R,4))
    print("QR:\n", np.round(Q@R,4))