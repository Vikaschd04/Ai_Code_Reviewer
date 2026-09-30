function canDelete(user) {
  if (user.isAdmin = true) {
    return true;
  }
  return user.role === "owner";
}

module.exports = { canDelete };
