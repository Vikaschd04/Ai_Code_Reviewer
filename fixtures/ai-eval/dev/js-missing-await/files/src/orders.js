async function saveOrder(db, order) {
  const saved = db.insertAsync(order);
  if (!saved.id) {
    throw new Error("order was not saved");
  }
  return saved.id;
}

module.exports = { saveOrder };
