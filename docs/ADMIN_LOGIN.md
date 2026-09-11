# La Serene HMS — Admin login

La Serene HMS uses a local username/password account and JWT session authentication. The first account created on a new installation is the `admin` role.

## First installation

1. Start the API and web app.
2. On the login screen choose **New installation? Create the first admin**.
3. Enter the username and password you want to use for the hotel administrator.
4. Choose **Create admin & sign in**.
5. The account is now an `admin` account and can access all HMS modules, including Rooms, Reservations, Billing, Reports, Night Audit and Backup.

The password is never stored as plain text; the API hashes it before saving it.

## Existing installation

Use the normal **Welcome back** login screen with the administrator username and the password created during bootstrap.

If the header currently shows a username such as `admin123` with role `admin`, that is the username of the signed-in administrator. The password is the password that was originally chosen for that account; it cannot be inferred from the username.

## Important

There is deliberately no hard-coded production admin password in the repository. Do not add one to the code or `.env` file.

If the administrator password is forgotten, reset it through a controlled local database/admin recovery procedure rather than creating a public/default password.
