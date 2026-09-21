create table users(id integer primary key, email text, tier text);
create table products(sku text primary key, price real);
create table cart(user_id integer, sku text, qty integer);
create table orders(id integer primary key, user_id integer, sku text, qty integer);
create table payments(id integer primary key, user_id integer, amount real, refunded integer default 0);
