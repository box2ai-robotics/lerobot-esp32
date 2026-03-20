#!/usr/bin/env python3
"""
SO-100 机械臂正逆运动学 — 纯 Python + NumPy 实现

无需 C 编译器，可替代 lerobot-kinematics 的 FK/IK 功能。

运动学参数来源: lerobot-kinematics SO-100 模型 (ETS 链)

4 DOF 运动链 (Joint 1~4 对应 qpos[0:4]):
  base → tx(0.02943) → tz(0.05504) → Ry(q1)    肩部俯仰
       → tx(0.1127)  → tz(-0.02798) → Ry(q2)   肘部
       → tx(0.13504) → tz(0.00519) → Ry(q3)     腕部俯仰
       → tx(0.0593)  → tz(0.00996) → Rx(q4)     腕部翻转
       → end-effector

用法:
    from so100_kinematics import so100_FK, so100_IK

    # 正运动学
    qpos_4dof = [q1, q2, q3, q4]  # 弧度
    pose_6d = so100_FK(qpos_4dof)  # [x, y, z, roll, pitch, yaw]

    # 逆运动学
    q_solution, success = so100_IK(q_now_4dof, target_pose_6d)
"""

import numpy as np

# ============================================
# SO-100 运动学参数 (ETS 链)
# ============================================

# 连杆参数: [(tx, tz), ...] 每个关节前的平移
LINK_PARAMS = [
    (0.02943, 0.05504),   # base → joint1
    (0.1127, -0.02798),   # joint1 → joint2
    (0.13504, 0.00519),   # joint2 → joint3
    (0.0593, 0.00996),    # joint3 → end-effector
]

# 关节轴: 前 3 个关节绕 Y 轴 (Ry), 第 4 个关节绕 X 轴 (Rx)
JOINT_AXES = ['y', 'y', 'y', 'x']

# 关节限位 (弧度) — 运动学模型标准范围
JOINT_LIMITS_LOW = np.array([-np.pi, -0.2, -1.5, -np.pi])
JOINT_LIMITS_HIGH = np.array([0.2, np.pi, 1.5, np.pi])

# IK 求解时使用宽泛限位 (因为 raw→rad 没有 calibration，弧度可能超出标准范围)
IK_LIMITS_LOW = np.array([-2 * np.pi, -2 * np.pi, -2 * np.pi, -2 * np.pi])
IK_LIMITS_HIGH = np.array([2 * np.pi, 2 * np.pi, 2 * np.pi, 2 * np.pi])

# IK 求解参数
IK_MAX_ITER = 20
IK_MAX_SEARCH = 3
IK_TOL = 1e-3
IK_DAMPING = 0.5
IK_SMOOTH_MAX_STEP = 0.1  # rad/step


# ============================================
# 基础变换矩阵工具
# ============================================

def _tx(d):
    """沿 X 轴平移"""
    T = np.eye(4)
    T[0, 3] = d
    return T


def _tz(d):
    """沿 Z 轴平移"""
    T = np.eye(4)
    T[2, 3] = d
    return T


def _ry(theta):
    """绕 Y 轴旋转"""
    c, s = np.cos(theta), np.sin(theta)
    T = np.eye(4)
    T[0, 0] = c;  T[0, 2] = s
    T[2, 0] = -s; T[2, 2] = c
    return T


def _rx(theta):
    """绕 X 轴旋转"""
    c, s = np.cos(theta), np.sin(theta)
    T = np.eye(4)
    T[1, 1] = c;  T[1, 2] = -s
    T[2, 1] = s;  T[2, 2] = c
    return T


def _rot_func(axis):
    """根据轴名返回旋转函数"""
    return _ry if axis == 'y' else _rx


# ============================================
# 正运动学 (FK)
# ============================================

def _fkine(q):
    """计算 4 DOF 正运动学，返回 4x4 齐次变换矩阵"""
    T = np.eye(4)
    for i in range(4):
        tx_val, tz_val = LINK_PARAMS[i]
        T = T @ _tx(tx_val) @ _tz(tz_val) @ _rot_func(JOINT_AXES[i])(q[i])
    return T


def _rot_to_euler_xyz(R):
    """旋转矩阵 -> Euler 角 (XYZ 顺序): [roll, pitch, yaw]"""
    # beta = pitch
    sy = np.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    if sy > 1e-6:
        roll = np.arctan2(R[2, 1], R[2, 2])
        pitch = np.arctan2(-R[2, 0], sy)
        yaw = np.arctan2(R[1, 0], R[0, 0])
    else:
        roll = np.arctan2(-R[1, 2], R[1, 1])
        pitch = np.arctan2(-R[2, 0], sy)
        yaw = 0.0
    return np.array([roll, pitch, yaw])


def so100_FK(qpos_4dof):
    """SO-100 正运动学

    Args:
        qpos_4dof: 4 个关节角 (弧度), [q1, q2, q3, q4]

    Returns:
        [x, y, z, roll, pitch, yaw] — 与 lerobot_FK 兼容的 6D 位姿
        注意: 返回顺序为 [x, y, z, yaw, pitch, roll] 以兼容 lerobot-kinematics
    """
    q = np.array(qpos_4dof, dtype=float)
    T = _fkine(q)
    pos = T[:3, 3]
    R = T[:3, :3]
    rpy = _rot_to_euler_xyz(R)  # [roll, pitch, yaw]
    # lerobot_FK 返回顺序: [x, y, z, yaw, pitch, roll]
    return np.array([pos[0], pos[1], pos[2], rpy[2], rpy[1], rpy[0]])


# ============================================
# 雅可比矩阵 (数值微分)
# ============================================

def _jacobian(q, delta=1e-6):
    """数值雅可比矩阵 6x4 (位置 3 + 姿态 3)"""
    n = len(q)
    J = np.zeros((6, n))
    pose0 = so100_FK(q)
    for i in range(n):
        q_plus = q.copy()
        q_plus[i] += delta
        pose_plus = so100_FK(q_plus)
        J[:, i] = (pose_plus - pose0) / delta
    return J


# ============================================
# 逆运动学 (IK) — Levenberg-Marquardt
# ============================================

def _pose_error(current_pose, target_pose):
    """计算 6D 位姿误差"""
    return target_pose - current_pose


def _smooth_joint_motion(q_new, q_old, max_step=IK_SMOOTH_MAX_STEP):
    """限制关节速度变化"""
    diff = q_new - q_old
    diff = np.clip(diff, -max_step, max_step)
    return q_old + diff


def so100_IK(q_now, target_pose, max_iter=IK_MAX_ITER, tol=IK_TOL):
    """SO-100 逆运动学 (Levenberg-Marquardt)

    Args:
        q_now: 当前 4 DOF 关节角 (弧度), 作为初始猜测
        target_pose: 目标 6D 位姿 [x, y, z, yaw, pitch, roll]
                     (与 so100_FK 返回格式一致)
        max_iter: 最大迭代次数
        tol: 收敛精度

    Returns:
        (q_solution, success): 关节角解和是否成功
    """
    q_now = np.array(q_now, dtype=float)
    target_pose = np.array(target_pose, dtype=float)

    q = q_now.copy()
    We = np.diag([1.0, 1.0, 1.0, 0.5, 0.5, 0.5])  # 位置权重高于姿态

    for search in range(IK_MAX_SEARCH):
        q_iter = q.copy()
        for it in range(max_iter):
            current_pose = so100_FK(q_iter)
            e = _pose_error(current_pose, target_pose)
            error_norm = np.linalg.norm(e[:3])  # 位置误差

            if error_norm < tol:
                q_result = _smooth_joint_motion(q_iter, q_now)
                return q_result, True

            J = _jacobian(q_iter)
            JtWe = J.T @ We
            # LM 阻尼: (J^T W_e J + λI)^{-1} J^T W_e e
            lam = IK_DAMPING * error_norm
            H = JtWe @ J + lam * np.eye(4)
            try:
                dq = np.linalg.solve(H, JtWe @ e)
            except np.linalg.LinAlgError:
                break

            q_iter = q_iter + dq
            # 限位 (使用宽松范围，允许实际舵机位置)
            q_iter = np.clip(q_iter, IK_LIMITS_LOW, IK_LIMITS_HIGH)

        # 搜索失败，随机扰动重试
        if search < IK_MAX_SEARCH - 1:
            q = q_now + np.random.uniform(-0.2, 0.2, size=4)
            q = np.clip(q, IK_LIMITS_LOW, IK_LIMITS_HIGH)

    return np.full(4, -1.0), False


# ============================================
# 兼容接口 (drop-in replacement for lerobot-kinematics)
# ============================================

def lerobot_FK(qpos_4dof, robot=None):
    """兼容 lerobot-kinematics 的 FK 接口"""
    return so100_FK(qpos_4dof)


def lerobot_IK(q_now, target_pose, robot=None):
    """兼容 lerobot-kinematics 的 IK 接口"""
    return so100_IK(q_now, target_pose)


def get_robot(name='so100'):
    """兼容接口，返回 None (纯 Python 实现不需要 robot 对象)"""
    return None


# ============================================
# 测试
# ============================================

if __name__ == "__main__":
    print("SO-100 纯 Python 运动学测试")
    print("=" * 50)

    # 测试 FK
    q_test = np.array([-3.14, 3.14, 0.0, -1.57])
    pose = so100_FK(q_test)
    print(f"\nFK 测试:")
    print(f"  关节角 (rad): {q_test}")
    print(f"  末端位姿:     x={pose[0]:.4f} y={pose[1]:.4f} z={pose[2]:.4f}")
    print(f"                yaw={pose[3]:.4f} pitch={pose[4]:.4f} roll={pose[5]:.4f}")

    # 测试 IK (用 FK 结果作为目标)
    q_init = q_test + np.array([0.05, -0.05, 0.05, 0.05])
    q_sol, success = so100_IK(q_init, pose)
    print(f"\nIK 测试 (目标=FK结果):")
    print(f"  初始猜测: {q_init}")
    print(f"  IK 解:    {q_sol}")
    print(f"  成功:     {success}")

    # 验证 IK 解的 FK
    if success:
        pose_check = so100_FK(q_sol)
        err = np.linalg.norm(pose[:3] - pose_check[:3])
        print(f"  FK 验证:  位置误差={err:.6f}m")

    # 测试兼容接口
    print(f"\n兼容接口测试:")
    robot = get_robot('so100')
    pose2 = lerobot_FK(q_test, robot=robot)
    print(f"  lerobot_FK: {pose2}")
    q_sol2, ok2 = lerobot_IK(q_init, pose, robot=robot)
    print(f"  lerobot_IK: {q_sol2}, success={ok2}")

    print("\n[OK] 所有测试完成!")
