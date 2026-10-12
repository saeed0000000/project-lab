import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

const api = async (path, options={}) => {
  const token = localStorage.getItem("token");
  const headers = {"Content-Type":"application/json", ...(options.headers||{})};
  if (token) headers.Authorization = `Bearer ${token}`;
  const r = await fetch(path, {...options, headers});
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "Request failed");
  return data;
};

function Login({onLogin}) {
  const [email,setEmail]=useState("demo@finance.local");
  const [password,setPassword]=useState("Demo123!");
  const [error,setError]=useState("");
  const submit=async e=>{
    e.preventDefault(); setError("");
    try { const d=await api("/api/auth/login",{method:"POST",body:JSON.stringify({email,password})});
      localStorage.setItem("token",d.token); onLogin(d.user);
    } catch(err){setError(err.message)}
  };
  return <main className="center"><form className="card login" onSubmit={submit}>
    <h1>Finance Lab</h1><p>DevOps training application</p>
    <input value={email} onChange={e=>setEmail(e.target.value)} placeholder="Email"/>
    <input type="password" value={password} onChange={e=>setPassword(e.target.value)} placeholder="Password"/>
    <button>Sign in</button>{error&&<small>{error}</small>}
  </form></main>;
}

function Dashboard({user,onLogout}) {
  const [data,setData]=useState(null);
  const [form,setForm]=useState({accountId:"",description:"",amount:"",category:""});
  const load=()=>api("/api/dashboard").then(d=>{setData(d); if(!form.accountId&&d.accounts[0]) setForm(f=>({...f,accountId:d.accounts[0].id}))});
  useEffect(load,[]);
  if(!data) return <main className="center">Loading...</main>;
  const submit=async e=>{
    e.preventDefault();
    await api("/api/transactions",{method:"POST",body:JSON.stringify({...form,amount:Number(form.amount),accountId:Number(form.accountId)})});
    setForm(f=>({...f,description:"",amount:"",category:""})); load();
  };
  return <main className="page">
    <header><div><h1>Finance Dashboard</h1><p>Welcome, {user.name}</p></div><button onClick={onLogout}>Logout</button></header>
    <section className="grid">
      <div className="card"><span>Total balance</span><strong>${data.totalBalance.toFixed(2)}</strong></div>
      {data.accounts.map(a=><div className="card" key={a.id}><span>{a.name}</span><strong>${Number(a.balance).toFixed(2)}</strong><small>{a.type}</small></div>)}
    </section>
    <section className="two">
      <div className="card"><h2>Recent transactions</h2><table><thead><tr><th>Description</th><th>Category</th><th>Amount</th></tr></thead><tbody>
      {data.transactions.map(t=><tr key={t.id}><td>{t.description}</td><td>{t.category}</td><td className={Number(t.amount)<0?"negative":"positive"}>${Number(t.amount).toFixed(2)}</td></tr>)}
      </tbody></table></div>
      <form className="card" onSubmit={submit}><h2>Add transaction</h2>
        <select value={form.accountId} onChange={e=>setForm({...form,accountId:e.target.value})}>{data.accounts.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select>
        <input placeholder="Description" value={form.description} onChange={e=>setForm({...form,description:e.target.value})}/>
        <input type="number" step="0.01" placeholder="Amount (+ income / - expense)" value={form.amount} onChange={e=>setForm({...form,amount:e.target.value})}/>
        <input placeholder="Category" value={form.category} onChange={e=>setForm({...form,category:e.target.value})}/>
        <button>Add transaction</button>
      </form>
    </section>
  </main>;
}

function App(){
  const [user,setUser]=useState(null);
  useEffect(()=>{if(localStorage.getItem("token")) api("/api/dashboard").then(()=>setUser({name:"Demo User"})).catch(()=>localStorage.removeItem("token"))},[]);
  if(!user) return <Login onLogin={setUser}/>;
  return <Dashboard user={user} onLogout={()=>{localStorage.removeItem("token");setUser(null)}}/>;
}
createRoot(document.getElementById("root")).render(<App/>);
