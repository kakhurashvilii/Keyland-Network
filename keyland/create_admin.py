from getpass import getpass

from keyland.database import init_db, create_user, get_user_by_username
from keyland.auth import hash_password


def main():
    init_db()

    username = input("Admin username: ").strip()

    if len(username) < 3 or len(username) > 32:
        print("Username must contain 3-32 characters.")
        return

    if get_user_by_username(username):
        print("This username already exists.")
        return

    password = getpass("Admin password: ")
    confirm = getpass("Confirm password: ")

    if password != confirm:
        print("Passwords do not match.")
        return

    if len(password) < 8:
        print("Password must contain at least 8 characters.")
        return

    create_user(
        username=username,
        password_hash=hash_password(password),
        role="admin",
    )

    print("Administrator created successfully.")


if __name__ == "__main__":
    main()
