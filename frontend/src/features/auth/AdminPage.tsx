import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";

import { ApiError } from "../../api/client";
import { useCreateInvite, useInvites, useMe, useRevokeInvite, useSetBlocked, useUsers } from "../../api/hooks";
import { Loading } from "../../components/ProblemNotice";
import { AUTH_ERROR, label, ROLE_NAME, INVITE_STATE } from "../../strings/ko";

/** 초대 발급과 계정 관리. 관리자도 다른 사람의 영상과 결과는 열람할 수 없다. */
export function AdminPage() {
  const me = useMe();
  const invites = useInvites();
  const users = useUsers();
  const create = useCreateInvite();
  const revoke = useRevokeInvite();
  const block = useSetBlocked();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"participant" | "admin">("participant");
  const [link, setLink] = useState<string | null>(null);

  if (me.isPending) return <Loading />;
  if (me.data && me.data.role !== "admin") return <Navigate to="/" replace />;

  function submit(e: FormEvent) {
    e.preventDefault();
    create.mutate({ email: email.trim(), role }, { onSuccess: (r) => { setLink(`${window.location.origin}${r.accept_path}`); setEmail(""); } });
  }

  const err = create.error instanceof ApiError ? (AUTH_ERROR[create.error.code] ?? create.error.message) : null;

  return (
    <>
      <h1>계정 관리</h1>

      <section aria-labelledby="invite-title">
        <h2 id="invite-title">초대</h2>
        <form className="card" onSubmit={submit}>
          <p>
            <label>
              이메일 <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>{" "}
            <label>
              역할{" "}
              <select value={role} onChange={(e) => setRole(e.target.value as "participant" | "admin")}>
                <option value="participant">{ROLE_NAME.participant}</option>
                <option value="admin">{ROLE_NAME.admin}</option>
              </select>
            </label>{" "}
            <button type="submit" disabled={create.isPending}>초대 만들기</button>
          </p>
          {err ? <p className="notice notice--error" role="alert">{err}</p> : null}
        </form>
        {link ? (
          <p className="notice notice--hold" role="status">
            초대 링크는 지금만 볼 수 있습니다. 초대받는 사람에게 직접 전달해 주세요.
            <br />
            <code>{link}</code>
          </p>
        ) : null}
        <ul className="member-list">
          {invites.data?.map((i) => (
            <li key={i.invite_id}>
              {i.email} · {label(ROLE_NAME, i.role)} · {label(INVITE_STATE, i.state)}{" "}
              {i.state === "pending" ? (
                <button type="button" onClick={() => revoke.mutate(i.invite_id)}>폐기</button>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="user-title">
        <h2 id="user-title">계정</h2>
        <ul className="member-list">
          {users.data?.map((u) => (
            <li key={u.user_id}>
              {u.display_name} ({u.email}) · {label(ROLE_NAME, u.role)} · {u.status === "blocked" ? "차단됨" : "사용 중"}{" "}
              {u.user_id !== me.data?.user_id ? (
                <button type="button" onClick={() => block.mutate({ id: u.user_id, blocked: u.status !== "blocked" })}>
                  {u.status === "blocked" ? "차단 해제" : "차단"}
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
